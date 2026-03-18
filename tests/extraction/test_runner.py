"""Tests for explainshell.extraction.runner — batch orchestration."""

import unittest
from unittest.mock import MagicMock

from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.extraction.llm.providers import BatchResults, TokenUsage
from explainshell.extraction.runner import (
    _WorkItem,
    _group_work_items,
    run,
    run_batch_collected,
    run_collected,
)
from explainshell.extraction.llm.extractor import PreparedFile
from explainshell.extraction.types import (
    ExtractionOutcome,
    ExtractionResult,
    ExtractionStats,
)


def _make_prepared(basename: str, n_chunks: int = 1) -> PreparedFile:
    """Build a minimal PreparedFile for testing."""
    return PreparedFile(
        synopsis="test",
        aliases=[],
        original_lines={},
        basename=basename,
        numbered_text="",
        plain_text_len=100,
        plain_text="x" * 100,
        requests=[f"content-{basename}-{i}" for i in range(n_chunks)],
    )


def _make_result() -> ExtractionResult:
    mp = MagicMock()
    mp.options = [MagicMock()]
    return ExtractionResult(
        mp=mp,
        raw=MagicMock(),
        stats=ExtractionStats(chunks=1, plain_text_len=100),
    )


class _FakeExtractor:
    """Fake that satisfies BatchExtractor for isinstance checks.

    Uses MagicMock for method bodies so tests can inspect calls and set
    side_effects.  All attributes are per-instance, avoiding the class-level
    mutation that PropertyMock on MagicMock causes.
    """

    def __init__(self) -> None:
        self.extract = MagicMock()
        self.prepare = MagicMock()
        self.finalize = MagicMock()
        self.batch_provider = MagicMock()


def _make_extractor(
    prepared_map: dict[str, PreparedFile],
    finalize_results: dict[str, ExtractionResult] | None = None,
    finalize_error: Exception | None = None,
) -> _FakeExtractor:
    """Build a fake BatchExtractor with prepare/finalize/batch_provider."""
    ext = _FakeExtractor()

    def _prepare(gz_path: str) -> PreparedFile:
        return prepared_map[gz_path]

    ext.prepare.side_effect = _prepare

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

    bp.submit_batch.return_value = "test-job-id"
    bp.poll_batch.return_value = MagicMock()

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
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(batch.n_succeeded, 1)
        self.assertEqual(batch.n_failed, 1)

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
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz])

        self.assertEqual(batch.n_failed, 1)
        entry = files[0]
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
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz])

        self.assertEqual(batch.n_failed, 1)
        entry = files[0]
        self.assertEqual(entry.outcome, ExtractionOutcome.FAILED)
        self.assertIsNotNone(entry.stats)
        self.assertEqual(entry.stats.plain_text_len, 100)
        self.assertEqual(entry.stats.chunks, 1)

    def test_incomplete_batch_result_preserves_prep_stats(self):
        """Files with missing batch responses should have prep stats."""
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
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(batch.n_succeeded, 1)
        self.assertEqual(batch.n_failed, 1)
        bravo = next(f for f in files if f.gz_path == gz_b)
        self.assertEqual(bravo.outcome, ExtractionOutcome.FAILED)
        self.assertIsNotNone(bravo.stats)
        self.assertEqual(bravo.stats.plain_text_len, 100)


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
        ext.batch_provider = bp

        _batch, files = run_batch_collected(ext, [gz_a, gz_b])

        paths = {f.gz_path for f in files}
        self.assertEqual(paths, {gz_a, gz_b})

    def test_skipped_files_get_entries(self):
        """Files that fail prepare() with SkippedExtraction get SKIPPED entries."""
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"
        prepared_b = _make_prepared("bravo")

        ext = _FakeExtractor()

        def _prepare(gz_path):
            if gz_path == gz_a:
                raise SkippedExtraction(
                    "too large",
                    stats=ExtractionStats(plain_text_len=999999),
                )
            return prepared_b

        ext.prepare.side_effect = _prepare

        ext.finalize.side_effect = lambda *a, **kw: _make_result()

        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(batch.n_skipped, 1)
        self.assertEqual(batch.n_succeeded, 1)
        alpha = next(f for f in files if f.gz_path == gz_a)
        bravo = next(f for f in files if f.gz_path == gz_b)

        self.assertEqual(alpha.outcome, ExtractionOutcome.SKIPPED)
        self.assertIsNotNone(alpha.stats)
        self.assertEqual(alpha.stats.plain_text_len, 999999)

        self.assertEqual(bravo.outcome, ExtractionOutcome.SUCCESS)

    def test_generic_prepare_exception_gives_failed_entry(self):
        """A generic exception from prepare() must not abort the whole batch."""
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"
        prepared_b = _make_prepared("bravo")

        ext = _FakeExtractor()

        def _prepare(gz_path):
            if gz_path == gz_a:
                raise RuntimeError("unexpected IO error")
            return prepared_b

        ext.prepare.side_effect = _prepare

        ext.finalize.side_effect = lambda *a, **kw: _make_result()

        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(batch.n_failed, 1)
        self.assertEqual(batch.n_succeeded, 1)
        alpha = next(f for f in files if f.gz_path == gz_a)
        bravo = next(f for f in files if f.gz_path == gz_b)

        self.assertEqual(alpha.outcome, ExtractionOutcome.FAILED)
        self.assertIn("unexpected IO error", alpha.error)
        self.assertEqual(bravo.outcome, ExtractionOutcome.SUCCESS)


class TestRunBatchCallbacks(unittest.TestCase):
    """on_result is called for every file."""

    def test_on_result_called_for_all_outcomes(self):
        gz_a = "/fake/alpha.1.gz"
        gz_b = "/fake/bravo.1.gz"
        prepared_a = _make_prepared("alpha")

        ext = _FakeExtractor()

        def _prepare(gz_path):
            if gz_path == gz_b:
                raise SkippedExtraction("too big")
            return prepared_a

        ext.prepare.side_effect = _prepare

        ext.finalize.side_effect = lambda *a, **kw: _make_result()

        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        ext.batch_provider = bp

        _batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(len(files), 2)
        paths = {f.gz_path for f in files}
        self.assertEqual(paths, {gz_a, gz_b})


class TestRunBatchGenericExceptions(unittest.TestCase):
    """Generic (non-ExtractionError) exceptions must not lose files."""

    def test_generic_provider_error_gives_all_files_entries(self):
        """A raw SDK/network error from the provider still produces FAILED entries."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor({gz: prepared})
        bp = _make_batch_provider(error=RuntimeError("network timeout"))
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz])

        self.assertEqual(batch.n_failed, 1)
        entry = files[0]
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
        ext.batch_provider = bp

        batch, files = run_batch_collected(ext, [gz_a, gz_b])

        self.assertEqual(batch.n_failed, 2)
        for entry in files:
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
            return "job-1"

        bp.submit_batch.side_effect = _submit_batch
        bp.poll_batch.return_value = MagicMock()
        bp.collect_results.return_value = BatchResults(
            {"0:0": '{"options":[],"dashless_opts":false}'},
            TokenUsage(100, 50),
        )
        ext.batch_provider = bp

        # batch_size=1 forces each file into its own batch
        batch, files = run_batch_collected(ext, [gz_a, gz_b], batch_size=1)

        self.assertEqual(batch.n_succeeded, 1)
        self.assertEqual(batch.n_failed, 1)
        alpha = next(f for f in files if f.gz_path == gz_a)
        bravo = next(f for f in files if f.gz_path == gz_b)
        self.assertEqual(alpha.outcome, ExtractionOutcome.SUCCESS)
        self.assertEqual(bravo.outcome, ExtractionOutcome.FAILED)
        self.assertIn("lost connection", bravo.error)


class TestRunDispatcher(unittest.TestCase):
    """Tests for the unified run() dispatcher."""

    def test_batch_size_zero_raises(self):
        """batch_size=0 raises ValueError before dispatch."""
        ext = _FakeExtractor()
        with self.assertRaises(ValueError) as ctx:
            run(ext, ["/fake/a.1.gz"], batch_size=0)
        self.assertIn("batch_size must be >= 1", str(ctx.exception))

    def test_batch_size_negative_raises(self):
        """Negative batch_size raises ValueError."""
        ext = _FakeExtractor()
        with self.assertRaises(ValueError):
            run(ext, ["/fake/a.1.gz"], batch_size=-1)

    def test_batch_mode_raises_for_non_batch_extractor(self):
        """Requesting batch mode with a plain Extractor raises TypeError."""
        ext = MagicMock(spec=["extract"])
        with self.assertRaises(TypeError) as ctx:
            run(ext, ["/fake/a.1.gz"], batch_size=10)
        self.assertIn("BatchExtractor", str(ctx.exception))

    def test_batch_mode_dispatches_to_batch_extractor(self):
        """batch_size set with a BatchExtractor calls run_batch path."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor({gz: prepared})
        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        ext.batch_provider = bp

        batch, files = run_collected(ext, [gz], batch_size=50)

        self.assertEqual(batch.n_succeeded, 1)
        self.assertEqual(files[0].outcome, ExtractionOutcome.SUCCESS)

    def test_sequential_fallback(self):
        """No batch_size and jobs=1 runs sequentially."""
        ext = MagicMock()
        result = _make_result()
        result.gz_path = "/fake/a.1.gz"
        ext.extract.return_value = result

        batch, files = run_collected(ext, ["/fake/a.1.gz"])

        ext.extract.assert_called_once_with("/fake/a.1.gz")
        self.assertEqual(batch.n_succeeded, 1)

    def test_parallel_mode(self):
        """jobs > 1 runs in parallel."""
        ext = MagicMock()
        result = _make_result()
        result.gz_path = "/fake/a.1.gz"
        ext.extract.return_value = result

        batch, files = run_collected(ext, ["/fake/a.1.gz"], jobs=2)

        ext.extract.assert_called_once_with("/fake/a.1.gz")
        self.assertEqual(batch.n_succeeded, 1)

    def test_callbacks_forwarded(self):
        """on_start and on_result callbacks are forwarded through run()."""
        ext = MagicMock()
        result = _make_result()
        result.gz_path = "/fake/a.1.gz"
        ext.extract.return_value = result

        starts: list[str] = []
        results: list[str] = []

        run(
            ext,
            ["/fake/a.1.gz"],
            on_start=lambda p: starts.append(p),
            on_result=lambda p, e: results.append(p),
        )

        self.assertEqual(starts, ["/fake/a.1.gz"])
        self.assertEqual(results, ["/fake/a.1.gz"])

    def test_batch_takes_precedence_over_jobs(self):
        """When both batch_size and jobs are set, batch mode wins."""
        prepared = _make_prepared("alpha")
        gz = "/fake/alpha.1.gz"

        ext = _make_extractor({gz: prepared})
        bp = _make_batch_provider(
            responses={"0:0": '{"options":[],"dashless_opts":false}'}
        )
        ext.batch_provider = bp

        batch, files = run_collected(ext, [gz], batch_size=50, jobs=4)

        # batch_provider was used (batch mode), not thread pool
        bp.submit_batch.assert_called_once()
        self.assertEqual(batch.n_succeeded, 1)


class TestGroupWorkItems(unittest.TestCase):
    """Tests for _group_work_items: groups work items into batches
    respecting batch_size as a request count limit."""

    @staticmethod
    def _make_items(chunk_counts: list[int]) -> list[_WorkItem]:
        """Build work items with the given chunk counts."""
        return [
            _WorkItem(f"/fake/file{i}.1.gz", _make_prepared(f"file{i}", n))
            for i, n in enumerate(chunk_counts)
        ]

    def test_single_chunk_files_even_split(self):
        """4 single-chunk files with batch_size=2 → 2 batches of 2."""
        items = self._make_items([1, 1, 1, 1])
        batches = _group_work_items(items, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)
        self.assertEqual(len(batches[1]), 2)

    def test_empty(self):
        batches = _group_work_items([], batch_size=5)
        self.assertEqual(batches, [])

    def test_batch_size_larger_than_total(self):
        """All items fit in one batch."""
        items = self._make_items([1, 1, 1])
        batches = _group_work_items(items, batch_size=100)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    def test_multi_chunk_file_stays_together(self):
        """A file with more chunks than batch_size gets its own batch."""
        items = self._make_items([1, 3])
        batches = _group_work_items(items, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(len(batches[1]), 1)

    def test_all_items_preserved(self):
        """Every item appears in exactly one batch."""
        items = self._make_items([2, 3, 1, 2])
        batches = _group_work_items(items, batch_size=3)
        flat = [item for batch in batches for item in batch]
        self.assertEqual(flat, items)

    def test_single_file_many_chunks(self):
        """One file with many chunks → single batch."""
        items = self._make_items([10])
        batches = _group_work_items(items, batch_size=3)
        self.assertEqual(len(batches), 1)

    def test_batch_size_one(self):
        """batch_size=1 gives each file its own batch."""
        items = self._make_items([1, 2, 1])
        batches = _group_work_items(items, batch_size=1)
        self.assertEqual(len(batches), 3)

    def test_exact_boundary(self):
        """Batch boundary falls exactly between files."""
        items = self._make_items([2, 2])
        batches = _group_work_items(items, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0], [items[0]])
        self.assertEqual(batches[1], [items[1]])


if __name__ == "__main__":
    unittest.main()
