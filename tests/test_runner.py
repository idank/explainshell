"""Tests for explainshell.extraction.runner — batch orchestration."""

import unittest
from unittest.mock import MagicMock, PropertyMock

from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.extraction.llm import PreparedFile
from explainshell.extraction.llm.providers import BatchResults, TokenUsage
from explainshell.extraction.runner import run_batch
from explainshell.extraction.types import (
    ExtractionResult,
    ExtractionStats,
    ExtractionOutcome,
)


def _make_prepared(basename: str, n_chunks: int = 1) -> PreparedFile:
    """Build a minimal PreparedFile for testing."""
    return PreparedFile(
        synopsis="test",
        aliases=[],
        chunks=[f"chunk-{i}" for i in range(n_chunks)],
        original_lines={},
        basename=basename,
        numbered_text="",
        n_chunks=n_chunks,
        plain_text_len=100,
        plain_text="x" * 100,
    )


def _make_result() -> ExtractionResult:
    mp = MagicMock()
    mp.options = [MagicMock()]
    return ExtractionResult(
        mp=mp,
        raw=MagicMock(),
        stats=ExtractionStats(chunks=1, plain_text_len=100),
    )


def _make_extractor(
    prepared_map: dict[str, PreparedFile],
    finalize_results: dict[str, ExtractionResult] | None = None,
    finalize_error: Exception | None = None,
) -> MagicMock:
    """Build a mock LLMExtractor with prepare/build_request/finalize/batch_provider."""
    ext = MagicMock()

    def _prepare(gz_path: str) -> PreparedFile:
        return prepared_map[gz_path]

    ext.prepare.side_effect = _prepare

    def _build_request(prepared: PreparedFile, chunk_idx: int) -> tuple[str, str]:
        return f"info-{chunk_idx}", f"content-{prepared.basename}-{chunk_idx}"

    ext.build_request.side_effect = _build_request

    if finalize_error:

        def _finalize(gz_path, prepared, responses):
            raise finalize_error

        ext.finalize.side_effect = _finalize
    elif finalize_results:

        def _finalize(gz_path, prepared, responses):
            return finalize_results[gz_path]

        ext.finalize.side_effect = _finalize
    else:

        def _finalize(gz_path, prepared, responses):
            return _make_result()

        ext.finalize.side_effect = _finalize

    return ext


def _make_batch_provider(
    responses: dict[str, str] | None = None,
    error: Exception | None = None,
) -> MagicMock:
    """Build a mock batch provider."""
    bp = MagicMock()
    bp.make_poll_client.return_value = MagicMock()

    job = MagicMock()
    job.id = "test-job-id"
    bp.submit_batch.return_value = job
    bp.poll_batch.return_value = job

    if error:
        bp.collect_results.side_effect = error
    else:
        usage = TokenUsage(input_tokens=500, output_tokens=200)
        bp.collect_results.return_value = BatchResults(responses or {}, usage)

    return bp


class TestRunBatchStatsContract(unittest.TestCase):
    """Finding 1: BatchResult.stats should only accumulate SUCCESS outcomes."""

    def test_stats_only_from_successful_files(self):
        """Token counts in BatchResult.stats come only from successful finalization."""
        prepared_a = _make_prepared("alpha")
        prepared_b = _make_prepared("bravo")
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"

        result_a = ExtractionResult(
            mp=MagicMock(),
            raw=MagicMock(),
            stats=ExtractionStats(chunks=1, plain_text_len=100),
        )
        result_a.mp.options = [MagicMock()]

        ext = _make_extractor(
            {gz_a: prepared_a, gz_b: prepared_b},
            finalize_results={gz_a: result_a},
        )
        # bravo will fail finalization
        original_side_effect = ext.finalize.side_effect

        def _finalize_with_error(gz_path, prepared, responses):
            if gz_path == gz_b:
                raise ValueError("finalize failed")
            return original_side_effect(gz_path, prepared, responses)

        ext.finalize.side_effect = _finalize_with_error

        bp = _make_batch_provider(
            responses={
                "0:0": '{"options":[],"dashless_opts":false}',
                "1:0": '{"options":[],"dashless_opts":false}',
            }
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz_a, gz_b])

        # Only alpha succeeded
        succeeded = [f for f in batch.files if f.outcome == ExtractionOutcome.SUCCESS]
        failed = [f for f in batch.files if f.outcome == ExtractionOutcome.FAILED]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(failed), 1)

        # BatchResult.stats includes batch-level token counts
        self.assertEqual(batch.stats.input_tokens, 500)
        self.assertEqual(batch.stats.output_tokens, 200)
        # Plus non-token stats from successful file
        self.assertEqual(batch.stats.chunks, 1)
        self.assertEqual(batch.stats.plain_text_len, 100)


class TestRunBatchFailedStats(unittest.TestCase):
    """Finding 2: Failed batch files should preserve prep-phase stats."""

    def test_finalize_failure_preserves_prep_stats(self):
        """When finalize() fails, the result should still have prep stats."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor(
            {gz: prepared},
            finalize_error=ValueError("finalize boom"),
        )
        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz])

        self.assertEqual(len(batch.files), 1)
        entry = batch.files[0]
        self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
        self.assertIsNotNone(entry.stats)
        self.assertEqual(entry.stats.plain_text_len, 100)
        self.assertEqual(entry.stats.chunks, 1)

    def test_batch_failure_preserves_prep_stats(self):
        """When the whole batch fails, FileEntries should have prep stats."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor({gz: prepared})
        bp = _make_batch_provider(error=ExtractionError("batch exploded"))
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz])

        self.assertEqual(len(batch.files), 1)
        entry = batch.files[0]
        self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
        self.assertIsNotNone(entry.stats)
        self.assertEqual(entry.stats.plain_text_len, 100)
        self.assertEqual(entry.stats.chunks, 1)

    def test_reconciliation_preserves_prep_stats(self):
        """End-of-run reconciliation entries should have prep stats."""
        prepared_a = _make_prepared("alpha")
        prepared_b = _make_prepared("bravo")
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"

        ext = _make_extractor(
            {gz_a: prepared_a, gz_b: prepared_b},
        )
        # Only return results for alpha, not bravo
        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz_a, gz_b])

        # alpha succeeded, bravo was never finalized
        bravo_entries = [f for f in batch.files if f.gz_path == gz_b]
        self.assertEqual(len(bravo_entries), 1)
        entry = bravo_entries[0]
        self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
        self.assertIsNotNone(entry.stats)
        self.assertEqual(entry.stats.plain_text_len, 100)


class TestRunBatchAllOutcomes(unittest.TestCase):
    """Every input file gets exactly one ExtractionResult."""

    def test_all_files_get_entries(self):
        """Even when some fail, every file gets an outcome."""
        prepared_a = _make_prepared("alpha")
        prepared_b = _make_prepared("bravo")
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"

        ext = _make_extractor({gz_a: prepared_a, gz_b: prepared_b})

        def _finalize(gz_path, prepared, responses):
            if gz_path == gz_b:
                raise ValueError("boom")
            return _make_result()

        ext.finalize.side_effect = _finalize

        bp = _make_batch_provider(
            responses={
                "0:0": '{"options":[],"dashless_opts":false}',
                "1:0": '{"options":[],"dashless_opts":false}',
            }
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz_a, gz_b])

        paths = {f.gz_path for f in batch.files}
        self.assertEqual(paths, {gz_a, gz_b})

    def test_skipped_files_get_entries(self):
        """Files that fail prepare() with SkippedExtraction get SKIPPED entries."""
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"
        prepared_b = _make_prepared("bravo")

        ext = MagicMock()

        def _prepare(gz_path):
            if gz_path == gz_a:
                raise SkippedExtraction(
                    "too large",
                    stats=ExtractionStats(plain_text_len=999999),
                )
            return prepared_b

        ext.prepare.side_effect = _prepare
        ext.build_request.side_effect = lambda p, i: (f"info-{i}", f"content-{i}")
        ext.finalize.side_effect = lambda *a, **kw: _make_result()

        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz_a, gz_b])

        self.assertEqual(len(batch.files), 2)
        alpha = next(f for f in batch.files if f.gz_path == gz_a)
        bravo = next(f for f in batch.files if f.gz_path == gz_b)

        self.assertEqual(alpha.outcome, ExtractionOutcome.SKIPPED)
        self.assertIsNotNone(alpha.stats)
        self.assertEqual(alpha.stats.plain_text_len, 999999)

        self.assertEqual(bravo.outcome, ExtractionOutcome.SUCCESS)


class TestRunBatchCallbacks(unittest.TestCase):
    """on_result is called for every file."""

    def test_on_result_called_for_all_outcomes(self):
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"
        prepared_a = _make_prepared("alpha")

        ext = MagicMock()

        def _prepare(gz_path):
            if gz_path == gz_b:
                raise SkippedExtraction("too big")
            return prepared_a

        ext.prepare.side_effect = _prepare
        ext.build_request.side_effect = lambda p, i: (f"info-{i}", f"content-{i}")
        ext.finalize.side_effect = lambda *a, **kw: _make_result()

        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        callback_entries: list[tuple[str, object]] = []

        def _on_result(gz_path, entry):
            callback_entries.append((gz_path, entry))

        run_batch(ext, [gz_a, gz_b], on_result=_on_result)

        self.assertEqual(len(callback_entries), 2)
        paths = {p for p, _ in callback_entries}
        self.assertEqual(paths, {gz_a, gz_b})


class TestRunBatchGenericExceptions(unittest.TestCase):
    """Generic (non-ExtractionError) exceptions must not lose files."""

    def test_generic_provider_error_gives_all_files_entries(self):
        """A raw SDK/network error from the provider still produces FAILED entries."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor({gz: prepared})
        bp = _make_batch_provider(error=RuntimeError("network timeout"))
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz])

        self.assertEqual(len(batch.files), 1)
        entry = batch.files[0]
        self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
        self.assertIn("network timeout", entry.error)
        self.assertIsNotNone(entry.stats)

    def test_make_poll_client_failure_gives_all_files_entries(self):
        """If make_poll_client() raises, every file gets a FAILED entry."""
        prepared_a = _make_prepared("alpha")
        prepared_b = _make_prepared("bravo")
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"

        ext = _make_extractor({gz_a: prepared_a, gz_b: prepared_b})
        bp = MagicMock()
        bp.make_poll_client.side_effect = RuntimeError("auth failed")
        type(ext).batch_provider = PropertyMock(return_value=bp)

        batch = run_batch(ext, [gz_a, gz_b])

        self.assertEqual(len(batch.files), 2)
        for entry in batch.files:
            self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
            self.assertIn("auth failed", entry.error)
            self.assertIsNotNone(entry.stats)

    def test_generic_error_in_middle_batch_continues(self):
        """If batch 1 succeeds but batch 2 raises a generic error,
        batch 1 files are SUCCESS and batch 2 files are FAILED."""
        prepared_a = _make_prepared("alpha")
        prepared_b = _make_prepared("bravo")
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"

        ext = _make_extractor({gz_a: prepared_a, gz_b: prepared_b})
        bp = MagicMock()
        bp.make_poll_client.return_value = MagicMock()

        call_count = {"n": 0}

        def _submit_batch(requests):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise ConnectionError("lost connection")
            job = MagicMock()
            job.id = "job-1"
            return job

        bp.submit_batch.side_effect = _submit_batch
        bp.poll_batch.return_value = MagicMock()
        bp.collect_results.return_value = BatchResults(
            {"0:0": '{"options":[],"dashless_opts":false}'},
            TokenUsage(100, 50),
        )
        type(ext).batch_provider = PropertyMock(return_value=bp)

        # batch_size=1 forces each file into its own batch
        batch = run_batch(ext, [gz_a, gz_b], batch_size=1)

        self.assertEqual(len(batch.files), 2)
        alpha = next(f for f in batch.files if f.gz_path == gz_a)
        bravo = next(f for f in batch.files if f.gz_path == gz_b)
        self.assertEqual(alpha.outcome, ExtractionOutcome.SUCCESS)
        self.assertEqual(bravo.outcome, ExtractionOutcome.FAILED)
        self.assertIn("lost connection", bravo.error)


if __name__ == "__main__":
    unittest.main()
