"""Execution strategies for running an extractor over multiple files.

All return BatchResult. Pure orchestration with no extraction logic,
no DB access, no CLI concerns.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Callable
from typing import Any, NamedTuple
from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.util import fmt_tokens
from explainshell.extraction.llm.extractor import BatchExtractor, PreparedFile
from explainshell.extraction.llm.providers import BatchEntry, TokenUsage
from explainshell.extraction.manifest import BatchManifest
from explainshell.extraction.types import (
    BatchResult,
    Extractor,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionStats,
)

logger = logging.getLogger(__name__)


class _InflightBatches:
    """Thread-safe registry of in-flight provider batches.

    Used to cancel batches on KeyboardInterrupt so they don't linger
    on the provider side after the process exits.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batches: list[tuple[Any, Any, str]] = []  # (bp, client, job_id)
        self.stop_event = threading.Event()

    def register(self, bp: Any, client: Any, job_id: str) -> None:
        with self._lock:
            self._batches.append((bp, client, job_id))

    def deregister(self, job_id: str) -> None:
        with self._lock:
            self._batches = [(b, c, j) for b, c, j in self._batches if j != job_id]

    def cancel_all(self) -> None:
        self.stop_event.set()
        with self._lock:
            batches = list(self._batches)
            self._batches.clear()
        for bp, client, job_id in batches:
            try:
                bp.cancel_batch(client, job_id)
                logger.info("cancelled in-flight batch %s", job_id)
            except Exception as e:
                logger.warning("failed to cancel batch %s: %s", job_id, e)


class WorkItem(NamedTuple):
    """A file that passed the prepare phase and is ready for batch extraction."""

    gz_path: str
    """Path to the gzipped manpage file."""

    prepared: PreparedFile
    """Output of the prepare phase (chunked prompts, metadata)."""


class _BatchOutput(NamedTuple):
    """Return value from processing one provider batch."""

    entries: list[ExtractionResult]
    """Per-file results (each has ``gz_path`` set)."""

    usage: TokenUsage
    """Aggregate token usage for this batch."""


def _tally(batch: BatchResult, entry: ExtractionResult) -> None:
    """Update batch counters and stats from a single file result."""
    if entry.outcome == ExtractionOutcome.SUCCESS:
        batch.stats += entry.stats
        batch.n_succeeded += 1
    elif entry.outcome == ExtractionOutcome.SKIPPED:
        batch.n_skipped += 1
    else:
        batch.n_failed += 1


def _extract_one(extractor: Extractor, gz_path: str) -> ExtractionResult:
    """Run extractor on a single file, catching all expected errors."""
    try:
        return extractor.extract(gz_path)
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
        _tally(batch, entry)
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
            _tally(batch, entry)
            if on_result:
                on_result(entry.gz_path, entry)
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    return batch


def group_work_items(
    work_items: list[WorkItem],
    batch_size: int,
) -> list[list[WorkItem]]:
    """Group work items into batches, respecting batch_size as a request count limit.

    Each work item stays whole (all its chunks in one batch).  A batch may
    exceed ``batch_size`` when a single file has more chunks than the limit.
    """
    batches: list[list[WorkItem]] = []
    current: list[WorkItem] = []
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


def _process_one_batch(
    extractor: BatchExtractor,
    batch_idx: int,
    total_batches: int,
    batch_items: list[WorkItem],
    inflight: _InflightBatches,
    manifest: BatchManifest,
) -> _BatchOutput:
    """Process a single provider batch: submit -> poll -> collect -> finalize.

    Never raises for normal batch/file failures — returns FAILED entries
    instead.  Only truly unexpected errors (``KeyboardInterrupt``,
    ``SystemExit``) propagate.
    """
    bp = extractor.batch_provider
    entries: list[ExtractionResult] = []
    finalized: set[str] = set()
    usage = TokenUsage()
    job_id: str | None = None
    batch_error: str | None = None

    def _add(entry: ExtractionResult) -> None:
        finalized.add(entry.gz_path)
        entries.append(entry)

    try:
        # Build batch requests.
        requests: list[BatchEntry] = []
        for item_idx, (gz_path, prepared) in enumerate(batch_items):
            for chunk_idx, user_content in enumerate(prepared.requests):
                requests.append(BatchEntry(f"{item_idx}:{chunk_idx}", user_content))

        total_chars = sum(len(req.user_content) for req in requests)
        logger.info(
            "submitting batch %d/%d (%d requests, %s chars)...",
            batch_idx,
            total_batches,
            len(requests),
            f"{total_chars:,}",
        )

        client = bp.make_poll_client()
        job_id = bp.submit_batch(requests)
        logger.info("batch %d/%d submitted: %s", batch_idx, total_batches, job_id)
        inflight.register(bp, client, job_id)

        # Persist the batch ID immediately so it survives crashes/interrupts.
        manifest.record_batch(
            batch_idx=batch_idx,
            batch_id=job_id,
            status="submitted",
            files=[item.gz_path for item in batch_items],
        )

        try:
            completed_job = bp.poll_batch(
                client, job_id, poll_interval=30, stop_event=inflight.stop_event
            )
        except KeyboardInterrupt:
            # Leave registered so cancel_all() can cancel the provider batch.
            raise
        except Exception:
            inflight.deregister(job_id)
            raise
        else:
            inflight.deregister(job_id)
        collected = bp.collect_results(completed_job)
        usage = collected.usage

        batch_complete_msg = (
            f"batch {batch_idx}/{total_batches} completed: "
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
                _add(
                    ExtractionResult(
                        gz_path=gz_path,
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
                _add(
                    ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error=str(e),
                        stats=_prep_stats(prepared),
                    ),
                )
                continue

            logger.info(
                "[%s] done: %d option(s)", gz_path, len(finalize_result.mp.options)
            )
            _add(finalize_result)

    except Exception as e:
        # FAILED entries for all unfinalized files in this batch.
        batch_error = str(e)
        logger.error("batch %d failed: %s", batch_idx, e)
        for gz_path, prepared in batch_items:
            if gz_path not in finalized:
                _add(
                    ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error=f"batch {batch_idx} failed: {e}",
                        stats=_prep_stats(prepared),
                    ),
                )

    # Record to manifest before releasing batch items.
    manifest.record_batch(
        batch_idx=batch_idx,
        batch_id=job_id,
        status="failed" if batch_error else "completed",
        files=[item.gz_path for item in batch_items],
        error=batch_error,
    )

    # Sanity check: every file in this batch should be finalized.
    batch_paths = {item.gz_path for item in batch_items}
    missing = batch_paths - finalized
    if missing:
        logger.error(
            "BUG: %d file(s) in batch %d were never finalized: %s",
            len(missing),
            batch_idx,
            sorted(missing),
        )

    # Release PreparedFile references for this batch.
    batch_items.clear()

    return _BatchOutput(entries=entries, usage=usage)


def run_batch(
    extractor: BatchExtractor,
    gz_files: list[str],
    batch_size: int = 50,
    jobs: int = 1,
    on_start: Callable[[str], None] | None = None,
    on_result: Callable[[str, ExtractionResult], None] | None = None,
    *,
    manifest: BatchManifest,
) -> BatchResult:
    """Run LLM extraction via provider batch API.

    Files are finalized as soon as their batch completes (per-batch),
    not after all batches finish.  The optional ``on_result`` callback
    is invoked immediately after each file is finalized, always from the
    main thread.

    When ``jobs > 1``, up to that many provider batches are submitted
    and polled concurrently via a thread pool.
    """
    result = BatchResult()

    # Phase 1: prepare all files.
    work_items: list[WorkItem] = []
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
            _tally(result, entry)
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
            _tally(result, entry)
            if on_result:
                on_result(gz_path, entry)
            continue
        work_items.append(WorkItem(gz_path, prepared))

    if not work_items:
        return result

    # Phase 2: submit in batches, finalizing files per-batch.
    total_requests = sum(item.prepared.n_chunks for item in work_items)
    total_files = len(work_items)
    batches = group_work_items(work_items, batch_size)
    del work_items  # references now owned by batches
    total_batches = len(batches)
    logger.info(
        "collected %d request(s) from %d file(s) in %d batch(es)",
        total_requests,
        total_files,
        total_batches,
    )

    manifest.set_total_batches(total_batches)

    def _handle_output(output: _BatchOutput) -> None:
        """Tally results and invoke callbacks in the main thread."""
        result.stats.input_tokens += output.usage.input_tokens
        result.stats.output_tokens += output.usage.output_tokens
        result.stats.reasoning_tokens += output.usage.reasoning_tokens
        for entry in output.entries:
            _tally(result, entry)
            if on_result:
                on_result(entry.gz_path, entry)

    inflight = _InflightBatches()

    if jobs <= 1:
        # Sequential: process batches inline.
        try:
            for batch_idx, batch_items in enumerate(batches, 1):
                output = _process_one_batch(
                    extractor,
                    batch_idx,
                    total_batches,
                    batch_items,
                    inflight,
                    manifest,
                )
                _handle_output(output)
        except KeyboardInterrupt:
            # Cancel remote batches and exit quickly. We don't attempt
            # to collect partial results from cancelled batches — the
            # token loss is negligible and keeping the interrupt path
            # simple is more important.
            logger.info("interrupted, cancelling in-flight batches...")
            inflight.cancel_all()
            raise
    else:
        # Parallel: rolling thread pool — at most `jobs` batches in flight.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
        try:
            pending: dict[concurrent.futures.Future[_BatchOutput], int] = {}
            batch_iter = iter(enumerate(batches, 1))

            def _submit_next() -> bool:
                """Submit next batch if available. Returns False when exhausted."""
                item = next(batch_iter, None)
                if item is None:
                    return False
                idx, items = item
                batches[idx - 1] = None  # type: ignore[call-overload]  # release ref
                f = executor.submit(
                    _process_one_batch,
                    extractor,
                    idx,
                    total_batches,
                    items,
                    inflight,
                    manifest,
                )
                pending[f] = idx
                return True

            # Seed the pool with up to `jobs` batches.
            for _ in range(jobs):
                if not _submit_next():
                    break

            # As each finishes, tally results and submit next.
            while pending:
                done, _ = concurrent.futures.wait(
                    pending, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for f in done:
                    _handle_output(f.result())
                    del pending[f]
                    _submit_next()
        except KeyboardInterrupt:
            # Cancel remote batches and exit quickly. We don't attempt
            # to collect partial results from cancelled batches — the
            # token loss is negligible and keeping the interrupt path
            # simple is more important.
            logger.info("interrupted, cancelling in-flight batches...")
            inflight.cancel_all()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    n_succeeded = result.n_succeeded
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
    manifest: BatchManifest | None = None,
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
            jobs=jobs,
            on_start=on_start,
            on_result=on_result,
            manifest=manifest,
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


def run_collected(
    extractor: Extractor,
    gz_files: list[str],
    *,
    batch_size: int | None = None,
    jobs: int = 1,
    on_start: Callable[[str], None] | None = None,
    manifest: BatchManifest | None = None,
) -> tuple[BatchResult, list[ExtractionResult]]:
    """Like ``run``, but collects per-file results into a list."""
    files: list[ExtractionResult] = []
    batch = run(
        extractor,
        gz_files,
        batch_size=batch_size,
        jobs=jobs,
        on_start=on_start,
        on_result=lambda _p, e: files.append(e),
        manifest=manifest,
    )
    return batch, files


def run_batch_collected(
    extractor: BatchExtractor,
    gz_files: list[str],
    batch_size: int = 50,
    jobs: int = 1,
    on_start: Callable[[str], None] | None = None,
    *,
    manifest: BatchManifest,
) -> tuple[BatchResult, list[ExtractionResult]]:
    """Like ``run_batch``, but collects per-file results into a list."""
    files: list[ExtractionResult] = []
    batch = run_batch(
        extractor,
        gz_files,
        batch_size=batch_size,
        jobs=jobs,
        on_start=on_start,
        on_result=lambda _p, e: files.append(e),
        manifest=manifest,
    )
    return batch, files
