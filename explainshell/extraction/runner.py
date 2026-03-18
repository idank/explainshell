"""Execution strategies for running an extractor over multiple files.

All return BatchResult. Pure orchestration with no extraction logic,
no DB access, no CLI concerns.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Callable
from typing import NamedTuple
from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.util import fmt_tokens
from explainshell.extraction.llm.extractor import BatchExtractor, PreparedFile
from explainshell.extraction.llm.providers import BatchEntry
from explainshell.extraction.types import (
    BatchResult,
    Extractor,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionStats,
)

logger = logging.getLogger(__name__)


class _WorkItem(NamedTuple):
    """A file that passed the prepare phase and is ready for batch extraction."""

    gz_path: str
    """Path to the gzipped manpage file."""

    prepared: PreparedFile
    """Output of the prepare phase (chunked prompts, metadata)."""


def _extract_one(extractor: Extractor, gz_path: str) -> ExtractionResult:
    """Run extractor on a single file, catching all expected errors."""
    try:
        entry = extractor.extract(gz_path)
        entry.gz_path = gz_path
        return entry
    except SkippedExtraction as e:
        return ExtractionResult(
            gz_path=gz_path,
            outcome=ExtractionOutcome.SKIPPED,
            stats=e.stats,
            error=e.reason,
        )
    except (ExtractionError, Exception) as e:
        return ExtractionResult(
            gz_path=gz_path,
            outcome=ExtractionOutcome.FAILED,
            error=str(e),
        )


def run_sequential(
    extractor: Extractor,
    gz_files: list[str],
    on_start: Callable[[str], None] | None = None,
    on_result: Callable[[str, ExtractionResult], None] | None = None,
) -> BatchResult:
    """Run extractor on each file sequentially."""
    batch = BatchResult()

    for gz_path in gz_files:
        if on_start:
            on_start(gz_path)

        entry = _extract_one(extractor, gz_path)
        if entry.outcome == ExtractionOutcome.SUCCESS:
            batch.stats += entry.stats
        batch.files.append(entry)
        if on_result:
            on_result(gz_path, entry)

    return batch


def run_parallel(
    extractor: Extractor,
    gz_files: list[str],
    jobs: int,
    on_start: Callable[[str], None] | None = None,
    on_result: Callable[[str, ExtractionResult], None] | None = None,
) -> BatchResult:
    """Run extractor on files using a thread pool."""
    batch = BatchResult()

    def _do_one(gz_path: str) -> ExtractionResult:
        if on_start:
            on_start(gz_path)
        return _extract_one(extractor, gz_path)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    try:
        futures = {executor.submit(_do_one, gz_path): gz_path for gz_path in gz_files}
        for future in concurrent.futures.as_completed(futures):
            entry = future.result()
            batch.files.append(entry)
            if entry.outcome == ExtractionOutcome.SUCCESS:
                batch.stats += entry.stats
            if on_result:
                on_result(entry.gz_path, entry)
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    return batch


def _group_work_items(
    work_items: list[_WorkItem],
    batch_size: int,
) -> list[list[_WorkItem]]:
    """Group work items into batches, respecting batch_size as a request count limit.

    Each work item stays whole (all its chunks in one batch).  A batch may
    exceed ``batch_size`` when a single file has more chunks than the limit.
    """
    batches: list[list[_WorkItem]] = []
    current: list[_WorkItem] = []
    current_size = 0
    for item in work_items:
        n = item.prepared.n_chunks
        if current and current_size + n > batch_size:
            batches.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += n
    if current:
        batches.append(current)
    return batches


def _prep_stats(prepared: PreparedFile) -> ExtractionStats:
    """Build an ExtractionStats from a PreparedFile's prep-phase metrics."""
    return ExtractionStats(
        chunks=prepared.n_chunks,
        plain_text_len=prepared.plain_text_len,
    )


def run_batch(
    extractor: BatchExtractor,
    gz_files: list[str],
    batch_size: int = 50,
    on_start: Callable[[str], None] | None = None,
    on_result: Callable[[str, ExtractionResult], None] | None = None,
) -> BatchResult:
    """Run LLM extraction via provider batch API.

    Files are finalized as soon as their batch completes (per-batch),
    not after all batches finish. The optional ``on_result`` callback
    is invoked immediately after each file is finalized.
    """
    result = BatchResult()

    # Phase 1: prepare all files.
    work_items: list[_WorkItem] = []
    for gz_path in gz_files:
        if on_start:
            on_start(gz_path)
        try:
            prepared = extractor.prepare(gz_path)
        except SkippedExtraction as e:
            entry = ExtractionResult(
                gz_path=gz_path,
                outcome=ExtractionOutcome.SKIPPED,
                stats=e.stats,
                error=e.reason,
            )
            result.files.append(entry)
            if on_result:
                on_result(gz_path, entry)
            continue
        except (ExtractionError, Exception) as e:
            logger.error("failed to prepare %s: %s", gz_path, e)
            entry = ExtractionResult(
                gz_path=gz_path,
                outcome=ExtractionOutcome.FAILED,
                error=str(e),
            )
            result.files.append(entry)
            if on_result:
                on_result(gz_path, entry)
            continue
        work_items.append(_WorkItem(gz_path, prepared))

    if not work_items:
        return result

    # Phase 2: submit in batches, finalizing files per-batch.
    total_requests = sum(item.prepared.n_chunks for item in work_items)
    total_files = len(work_items)
    batches = _group_work_items(work_items, batch_size)
    del work_items  # references now owned by batches
    logger.info("collected %d request(s) from %d file(s)", total_requests, total_files)
    bp = extractor.batch_provider
    try:
        client = bp.make_poll_client()
    except Exception as e:
        logger.error("failed to create batch poll client: %s", e)
        for batch_items in batches:
            for gz_path, prepared in batch_items:
                entry = ExtractionResult(
                    gz_path=gz_path,
                    outcome=ExtractionOutcome.FAILED,
                    error=f"batch poll client failed: {e}",
                    stats=_prep_stats(prepared),
                )
                result.files.append(entry)
                if on_result:
                    on_result(gz_path, entry)
        return result

    cumulative_requests = 0
    batch_start_time = time.time()

    for batch_idx, batch_items in enumerate(batches, 1):
        finalized_paths: set[str] = set()

        def _finalize(gz_path: str, entry: ExtractionResult) -> None:
            finalized_paths.add(gz_path)
            entry.gz_path = gz_path
            result.files.append(entry)
            if entry.outcome == ExtractionOutcome.SUCCESS:
                result.stats += entry.stats
            if on_result:
                on_result(gz_path, entry)

        # Build batch requests from this batch's work items.
        requests: list[BatchEntry] = []
        for item_idx, (gz_path, prepared) in enumerate(batch_items):
            for chunk_idx, user_content in enumerate(prepared.requests):
                requests.append(BatchEntry(f"{item_idx}:{chunk_idx}", user_content))

        total_chars = sum(len(req.user_content) for req in requests)
        logger.info(
            "submitting batch %d/%d (%d requests, %s chars)...",
            batch_idx,
            len(batches),
            len(requests),
            f"{total_chars:,}",
        )
        try:
            job_id = bp.submit_batch(requests)
            logger.info("batch %d/%d submitted: %s", batch_idx, len(batches), job_id)

            completed_job = bp.poll_batch(client, job_id)
            collected = bp.collect_results(completed_job)
            cumulative_requests += len(collected.responses)
            result.stats.input_tokens += collected.usage.input_tokens
            result.stats.output_tokens += collected.usage.output_tokens
            result.stats.reasoning_tokens += collected.usage.reasoning_tokens

            batch_complete_msg = (
                f"batch {batch_idx}/{len(batches)} completed: "
                f"{len(collected.responses)} result(s), "
                f"input={fmt_tokens(collected.usage.input_tokens)} tokens, "
                f"output={fmt_tokens(collected.usage.output_tokens)} tokens"
            )
            if collected.usage.reasoning_tokens:
                batch_complete_msg += (
                    f", reasoning={fmt_tokens(collected.usage.reasoning_tokens)} tokens"
                )
            logger.info(batch_complete_msg)

            # Finalize each file in this batch.
            for item_idx, (gz_path, prepared) in enumerate(batch_items):
                n_chunks = prepared.n_chunks
                responses: list[str] = []
                file_failed = False

                for chunk_idx in range(n_chunks):
                    key_str = f"{item_idx}:{chunk_idx}"
                    response_text = collected.responses.get(key_str)
                    if response_text is None:
                        logger.error(
                            "missing batch result for %s chunk %d", gz_path, chunk_idx
                        )
                        file_failed = True
                        break
                    responses.append(response_text)

                if file_failed:
                    _finalize(
                        gz_path,
                        ExtractionResult(
                            outcome=ExtractionOutcome.FAILED,
                            error="incomplete batch result: missing response for one or more chunks",
                            stats=_prep_stats(prepared),
                        ),
                    )
                    continue

                try:
                    finalize_result = extractor.finalize(gz_path, prepared, responses)
                except Exception as e:
                    logger.error("failed to finalize %s: %s", gz_path, e)
                    _finalize(
                        gz_path,
                        ExtractionResult(
                            outcome=ExtractionOutcome.FAILED,
                            error=str(e),
                            stats=_prep_stats(prepared),
                        ),
                    )
                    continue

                logger.info(
                    "[%s] done: %d option(s)", gz_path, len(finalize_result.mp.options)
                )
                _finalize(gz_path, finalize_result)

            # Cumulative progress summary.
            elapsed_m = int((time.time() - batch_start_time) / 60)
            n_succeeded = sum(
                1 for f in result.files if f.outcome == ExtractionOutcome.SUCCESS
            )
            progress_msg = (
                f"progress: {cumulative_requests}/{total_requests} requests done, "
                f"{n_succeeded} files extracted, "
                f"input={fmt_tokens(result.stats.input_tokens)} tokens, "
                f"output={fmt_tokens(result.stats.output_tokens)} tokens"
            )
            if result.stats.reasoning_tokens:
                progress_msg += (
                    f", reasoning={fmt_tokens(result.stats.reasoning_tokens)} tokens"
                )
            progress_msg += f", elapsed={elapsed_m}m"
            logger.info(progress_msg)

        except Exception as e:
            # Emit FAILED entries for all unfinalized files in this batch.
            logger.error("batch %d failed: %s", batch_idx, e)
            for gz_path, prepared in batch_items:
                if gz_path in finalized_paths:
                    continue
                _finalize(
                    gz_path,
                    ExtractionResult(
                        outcome=ExtractionOutcome.FAILED,
                        error=f"batch {batch_idx} failed: {e}",
                        stats=_prep_stats(prepared),
                    ),
                )

        # Sanity check: every file in this batch should be finalized.
        batch_paths = {item.gz_path for item in batch_items}
        missing = batch_paths - finalized_paths
        if missing:
            logger.error(
                "BUG: %d file(s) in batch %d were never finalized: %s",
                len(missing),
                batch_idx,
                sorted(missing),
            )

        # Release PreparedFile references for this batch.
        batch_items.clear()

    n_succeeded = sum(1 for f in result.files if f.outcome == ExtractionOutcome.SUCCESS)
    logger.info(
        "batch: %d/%d file(s) extracted successfully",
        n_succeeded,
        total_files,
    )
    return result


def run(
    extractor: Extractor,
    gz_files: list[str],
    *,
    batch_size: int | None = None,
    jobs: int = 1,
    on_start: Callable[[str], None] | None = None,
    on_result: Callable[[str, ExtractionResult], None] | None = None,
) -> BatchResult:
    """Unified dispatcher for all execution modes.

    - ``batch_size`` set → batch mode (requires ``BatchExtractor``).
    - ``jobs > 1`` → parallel mode via thread pool.
    - otherwise → sequential.
    """
    if batch_size is not None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1 (got {batch_size})")
        if not isinstance(extractor, BatchExtractor):
            raise TypeError(
                f"batch mode requires a BatchExtractor (got {type(extractor).__name__})"
            )
        return run_batch(
            extractor,
            gz_files,
            batch_size=batch_size,
            on_start=on_start,
            on_result=on_result,
        )
    if jobs > 1:
        return run_parallel(
            extractor,
            gz_files,
            jobs,
            on_start=on_start,
            on_result=on_result,
        )
    return run_sequential(
        extractor,
        gz_files,
        on_start=on_start,
        on_result=on_result,
    )
