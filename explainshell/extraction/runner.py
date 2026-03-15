"""Execution strategies for running an extractor over multiple files.

All return BatchResult. Pure orchestration with no extraction logic,
no DB access, no CLI concerns.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explainshell.extraction.llm import PreparedFile

from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.extraction.types import (
    BatchExtractor,
    BatchResult,
    Extractor,
    ExtractionResult,
    ExtractionStats,
    ExtractionOutcome,
)

logger = logging.getLogger(__name__)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


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


def _build_chunk_aligned_batches(
    all_requests: list[tuple[str, str]],
    key_to_location: dict[str, tuple[int, int]],
    batch_size: int,
) -> list[list[tuple[str, str]]]:
    """Split requests into batches, keeping all chunks of one file together."""
    batches: list[list[tuple[str, str]]] = []
    i = 0
    while i < len(all_requests):
        end = min(i + batch_size, len(all_requests))
        if end < len(all_requests):
            last_work_idx, _ = key_to_location[all_requests[end - 1][0]]
            while end < len(all_requests):
                next_work_idx, _ = key_to_location[all_requests[end][0]]
                if next_work_idx != last_work_idx:
                    break
                end += 1
        batches.append(all_requests[i:end])
        i = end
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
    batch = BatchResult()

    # Phase 1: prepare all files.
    work_items: list[tuple[int, str, PreparedFile]] = []
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
            batch.files.append(entry)
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
            batch.files.append(entry)
            if on_result:
                on_result(gz_path, entry)
            continue
        work_items.append((len(work_items), gz_path, prepared))

    if not work_items:
        return batch

    # Phase 2: collect all (key, user_content) pairs.
    all_requests: list[tuple[str, str]] = []
    key_to_location: dict[str, tuple[int, int]] = {}
    for work_idx, gz_path, prepared in work_items:
        for chunk_idx, user_content in enumerate(prepared.requests):
            key_str = f"{work_idx}:{chunk_idx}"
            all_requests.append((key_str, user_content))
            key_to_location[key_str] = (work_idx, chunk_idx)

    logger.info(
        "collected %d request(s) from %d file(s)", len(all_requests), len(work_items)
    )

    # Phase 3: submit in batches, finalizing files per-batch.
    batches = _build_chunk_aligned_batches(all_requests, key_to_location, batch_size)
    bp = extractor.batch_provider
    try:
        client = bp.make_poll_client()
    except Exception as e:
        logger.error("failed to create batch poll client: %s", e)
        for work_idx, gz_path, prepared in work_items:
            entry = ExtractionResult(
                gz_path=gz_path,
                outcome=ExtractionOutcome.FAILED,
                error=f"batch poll client failed: {e}",
                stats=_prep_stats(prepared),
            )
            batch.files.append(entry)
            if on_result:
                on_result(gz_path, entry)
        return batch

    all_results: dict[str, str] = {}
    finalized_indices: set[int] = set()
    cumulative_requests = 0
    batch_start_time = time.time()

    for batch_idx, batch_chunk in enumerate(batches, 1):
        total_chars = sum(len(uc) for _, uc in batch_chunk)
        logger.info(
            "submitting batch %d/%d (%d requests, %s chars)...",
            batch_idx,
            len(batches),
            len(batch_chunk),
            f"{total_chars:,}",
        )
        try:
            job = bp.submit_batch(batch_chunk)
            job_id = job.name if hasattr(job, "name") else job.id
            logger.info("batch %d/%d submitted: %s", batch_idx, len(batches), job_id)

            completed_job = bp.poll_batch(client, job_id)
            collected = bp.collect_results(completed_job)
            all_results.update(collected.responses)
            cumulative_requests += len(collected.responses)
            batch.stats.input_tokens += collected.usage.input_tokens
            batch.stats.output_tokens += collected.usage.output_tokens
            batch.stats.reasoning_tokens += collected.usage.reasoning_tokens

            batch_complete_msg = (
                f"batch {batch_idx}/{len(batches)} completed: "
                f"{len(collected.responses)} result(s), "
                f"input={_fmt_tokens(collected.usage.input_tokens)} tokens, "
                f"output={_fmt_tokens(collected.usage.output_tokens)} tokens"
            )
            if collected.usage.reasoning_tokens:
                batch_complete_msg += f", reasoning={_fmt_tokens(collected.usage.reasoning_tokens)} tokens"
            logger.info(batch_complete_msg)

            # Finalize files that now have all chunks available.
            for work_idx, gz_path, prepared in work_items:
                if work_idx in finalized_indices:
                    continue
                n_chunks = prepared.n_chunks
                if not all(f"{work_idx}:{ci}" in all_results for ci in range(n_chunks)):
                    continue

                finalized_indices.add(work_idx)
                responses: list[str] = []
                file_failed = False

                for chunk_idx in range(n_chunks):
                    key_str = f"{work_idx}:{chunk_idx}"
                    response_text = all_results.get(key_str)
                    if response_text is None:
                        logger.error(
                            "missing batch result for %s chunk %d", gz_path, chunk_idx
                        )
                        file_failed = True
                        break
                    responses.append(response_text)

                if file_failed:
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error="missing batch result for one or more chunks",
                        stats=_prep_stats(prepared),
                    )
                    batch.files.append(entry)
                    if on_result:
                        on_result(gz_path, entry)
                    continue

                try:
                    result = extractor.finalize(gz_path, prepared, responses)
                except Exception as e:
                    logger.error("failed to finalize %s: %s", gz_path, e)
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error=str(e),
                        stats=_prep_stats(prepared),
                    )
                    batch.files.append(entry)
                    if on_result:
                        on_result(gz_path, entry)
                    continue

                entry = result
                entry.gz_path = gz_path
                batch.files.append(entry)
                batch.stats += entry.stats
                logger.info("[%s] done: %d option(s)", gz_path, len(entry.mp.options))
                if on_result:
                    on_result(gz_path, entry)

            # Cumulative progress summary.
            elapsed_m = int((time.time() - batch_start_time) / 60)
            n_succeeded = sum(
                1 for f in batch.files if f.outcome == ExtractionOutcome.SUCCESS
            )
            progress_msg = (
                f"progress: {cumulative_requests}/{len(all_requests)} requests done, "
                f"{n_succeeded} files extracted, "
                f"input={_fmt_tokens(batch.stats.input_tokens)} tokens, "
                f"output={_fmt_tokens(batch.stats.output_tokens)} tokens"
            )
            if batch.stats.reasoning_tokens:
                progress_msg += (
                    f", reasoning={_fmt_tokens(batch.stats.reasoning_tokens)} tokens"
                )
            progress_msg += f", elapsed={elapsed_m}m"
            logger.info(progress_msg)

        except Exception as e:
            # Emit FAILED entries for all files in this batch that haven't
            # been finalized yet, so every file gets an outcome.
            logger.error("batch %d failed: %s", batch_idx, e)
            batch_work_indices = {key_to_location[key][0] for key, _ in batch_chunk}
            for work_idx, gz_path, prepared in work_items:
                if work_idx in finalized_indices:
                    continue
                if work_idx not in batch_work_indices:
                    continue
                finalized_indices.add(work_idx)
                entry = ExtractionResult(
                    gz_path=gz_path,
                    outcome=ExtractionOutcome.FAILED,
                    error=f"batch {batch_idx} failed: {e}",
                    stats=_prep_stats(prepared),
                )
                batch.files.append(entry)
                if on_result:
                    on_result(gz_path, entry)

    # End-of-run reconciliation: any work item that was never finalized
    # (e.g. its batch never ran or partially failed) gets a FAILED entry.
    for work_idx, gz_path, prepared in work_items:
        if work_idx not in finalized_indices:
            finalized_indices.add(work_idx)
            entry = ExtractionResult(
                gz_path=gz_path,
                outcome=ExtractionOutcome.FAILED,
                error="file was never finalized (batch may not have run)",
                stats=_prep_stats(prepared),
            )
            batch.files.append(entry)
            if on_result:
                on_result(gz_path, entry)

    n_succeeded = sum(1 for f in batch.files if f.outcome == ExtractionOutcome.SUCCESS)
    logger.info(
        "batch: %d/%d file(s) extracted successfully",
        n_succeeded,
        len(work_items),
    )
    return batch


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
