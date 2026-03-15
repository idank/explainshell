"""Unit tests for explainshell.manager."""

import argparse
import unittest
from unittest.mock import MagicMock, patch

from explainshell.extraction.runner import _build_chunk_aligned_batches
from explainshell.extraction.types import (
    ExtractionResult,
    ExtractionStats,
    ExtractionOutcome,
)


# ---------------------------------------------------------------------------
# TestBuildChunkAlignedBatches
# ---------------------------------------------------------------------------


class TestBuildChunkAlignedBatches(unittest.TestCase):
    """Tests for _build_chunk_aligned_batches: batch boundaries must never
    split a file's chunk group."""

    @staticmethod
    def _make_requests(file_chunk_counts):
        """Build (all_requests, key_to_location) for files with the given
        chunk counts.  E.g. [1, 3, 1] → file 0 has 1 chunk, file 1 has 3, etc.
        """
        all_requests = []
        key_to_location = {}
        for work_idx, n_chunks in enumerate(file_chunk_counts):
            for chunk_idx in range(n_chunks):
                key_str = f"{work_idx}:{chunk_idx}"
                all_requests.append((key_str, f"content-{key_str}"))
                key_to_location[key_str] = (work_idx, chunk_idx)
        return all_requests, key_to_location

    def _work_indices_in_batch(self, batch, key_to_location):
        """Return the set of work_idx values present in a batch."""
        return {key_to_location[key][0] for key, _ in batch}

    # -- basic cases --

    def test_single_chunk_files_even_split(self):
        """4 single-chunk files with batch_size=2 → 2 batches of 2."""
        reqs, k2l = self._make_requests([1, 1, 1, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)
        self.assertEqual(len(batches[1]), 2)

    def test_empty_requests(self):
        batches = _build_chunk_aligned_batches([], {}, batch_size=5)
        self.assertEqual(batches, [])

    def test_batch_size_larger_than_total(self):
        """All requests fit in one batch."""
        reqs, k2l = self._make_requests([1, 1, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=100)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    # -- chunk straddling --

    def test_multi_chunk_file_not_split(self):
        """File with 3 chunks straddling a batch_size=2 boundary stays together."""
        # File 0: 1 chunk, File 1: 3 chunks → requests: [0:0, 1:0, 1:1, 1:2]
        reqs, k2l = self._make_requests([1, 3])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        # Batch boundary at index 2 would split file 1's chunks.
        # The algorithm should extend batch 1 to include all of file 1.
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 4)

    def test_straddling_extends_batch(self):
        """Batch boundary falls mid-file → batch is extended."""
        # File 0: 2 chunks, File 1: 2 chunks, File 2: 2 chunks
        # batch_size=3 → naive split: [0:0, 0:1, 1:0] | [1:1, 2:0, 2:1]
        # File 1 straddles → extend batch 1 to include 1:1 → size 4
        reqs, k2l = self._make_requests([2, 2, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        self.assertEqual(len(batches), 2)
        # First batch: file 0 (2) + file 1 (2) = 4
        self.assertEqual(len(batches[0]), 4)
        # Second batch: file 2 (2)
        self.assertEqual(len(batches[1]), 2)

    def test_every_file_stays_in_one_batch(self):
        """Property test: no file's chunks appear in more than one batch."""
        reqs, k2l = self._make_requests([1, 3, 2, 1, 4, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        seen_files = set()
        for batch in batches:
            batch_files = self._work_indices_in_batch(batch, k2l)
            # No file should appear in a previous batch
            self.assertEqual(
                batch_files & seen_files,
                set(),
                f"Files {batch_files & seen_files} appear in multiple batches",
            )
            seen_files |= batch_files
        # All files accounted for
        self.assertEqual(seen_files, {0, 1, 2, 3, 4, 5})

    def test_all_requests_preserved(self):
        """Every request appears in exactly one batch."""
        reqs, k2l = self._make_requests([2, 3, 1, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        flat = [item for batch in batches for item in batch]
        self.assertEqual(flat, reqs)

    # -- edge cases --

    def test_single_file_many_chunks(self):
        """One file with many chunks → single batch."""
        reqs, k2l = self._make_requests([10])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=3)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 10)

    def test_batch_size_one(self):
        """batch_size=1 with multi-chunk files still groups them."""
        reqs, k2l = self._make_requests([1, 2, 1])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=1)
        # File 0: 1 chunk → batch of 1
        # File 1: 2 chunks → batch of 2 (extended from size 1)
        # File 2: 1 chunk → batch of 1
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(len(batches[1]), 2)
        self.assertEqual(len(batches[2]), 1)

    def test_exact_boundary(self):
        """Batch boundary falls exactly between files → no extension needed."""
        # File 0: 2 chunks, File 1: 2 chunks → batch_size=2
        reqs, k2l = self._make_requests([2, 2])
        batches = _build_chunk_aligned_batches(reqs, k2l, batch_size=2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 2)
        self.assertEqual(len(batches[1]), 2)
        # Verify each batch has exactly one file
        self.assertEqual(self._work_indices_in_batch(batches[0], k2l), {0})
        self.assertEqual(self._work_indices_in_batch(batches[1], k2l), {1})


# ---------------------------------------------------------------------------
# TestBatchPerBatchDbWrites
# ---------------------------------------------------------------------------


class TestBatchPerBatchDbWrites(unittest.TestCase):
    """Verify the manager writes results to the DB via on_result callback
    from run_batch."""

    def _make_args(self, batch_size=2):
        return argparse.Namespace(
            mode="llm:openai/test-model",
            db="/tmp/test.db",
            overwrite=False,
            drop=False,
            dry_run=False,
            diff=None,
            debug_dir=None,
            log="WARNING",
            jobs=1,
            batch=batch_size,
            files=[],
        )

    @patch("explainshell.manager.run_batch")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_db_writes_after_each_batch(
        self,
        mock_source,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run_batch,
    ):
        """Verify on_result callback writes to DB for each successful file."""
        gz_files = [
            "/fake/distro/release/1/alpha.1.gz",
            "/fake/distro/release/1/bravo.1.gz",
            "/fake/distro/release/1/charlie.1.gz",
            "/fake/distro/release/1/delta.1.gz",
        ]
        mock_collect.return_value = gz_files
        mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        from explainshell import errors as _errors

        mock_store.find_man_page.side_effect = _errors.ProgramDoesNotExist("x")

        mock_make_ext.return_value = MagicMock()

        # When run_batch is called, simulate per-file callbacks
        writes_at_callback = []

        def _fake_run_batch(ext, files, batch_size, on_start=None, on_result=None):
            from explainshell.extraction.types import BatchResult

            batch = BatchResult()
            for gz_path in files:
                if on_start:
                    on_start(gz_path)
                fake_mp = MagicMock()
                fake_mp.options = [MagicMock()]
                entry = ExtractionResult(
                    gz_path=gz_path,
                    outcome=ExtractionOutcome.SUCCESS,
                    mp=fake_mp,
                    raw=MagicMock(),
                    stats=ExtractionStats(),
                )
                batch.files.append(entry)
                if on_result:
                    writes_at_callback.append(mock_store.add_manpage.call_count)
                    on_result(gz_path, entry)
            return batch

        mock_run_batch.side_effect = _fake_run_batch

        from explainshell.manager import main

        args = self._make_args(batch_size=2)
        main(args)

        # on_result is called 4 times (once per file), and each call writes to DB
        self.assertEqual(mock_store.add_manpage.call_count, 4)
        # Writes are incremental: 0 before first, 1 before second, etc.
        self.assertEqual(writes_at_callback, [0, 1, 2, 3])

    @patch("explainshell.manager.run_batch")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_batch2_failure_preserves_batch1_writes(
        self,
        mock_source,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run_batch,
    ):
        """If some files fail, successful files must still be in the DB."""
        gz_files = [
            "/fake/distro/release/1/alpha.1.gz",
            "/fake/distro/release/1/bravo.1.gz",
            "/fake/distro/release/1/charlie.1.gz",
            "/fake/distro/release/1/delta.1.gz",
        ]
        mock_collect.return_value = gz_files
        mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        from explainshell import errors as _errors

        mock_store.find_man_page.side_effect = _errors.ProgramDoesNotExist("x")
        mock_make_ext.return_value = MagicMock()

        def _fake_run_batch(ext, files, batch_size, on_start=None, on_result=None):
            from explainshell.extraction.types import BatchResult

            batch = BatchResult()
            for i, gz_path in enumerate(files):
                if on_start:
                    on_start(gz_path)
                if i < 2:
                    # First 2 files succeed
                    fake_mp = MagicMock()
                    fake_mp.options = [MagicMock()]
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=fake_mp,
                        raw=MagicMock(),
                        stats=ExtractionStats(),
                    )
                else:
                    # Last 2 files fail
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error="batch failed",
                    )
                batch.files.append(entry)
                if on_result:
                    on_result(gz_path, entry)
            return batch

        mock_run_batch.side_effect = _fake_run_batch

        from explainshell.manager import main

        args = self._make_args(batch_size=2)
        ret = main(args)

        # Only 2 successful files were written
        self.assertEqual(mock_store.add_manpage.call_count, 2)
        # Return code is non-zero because some files failed
        self.assertNotEqual(ret, 0)


# ---------------------------------------------------------------------------
# TestLlmManagerDryRun
# ---------------------------------------------------------------------------


class TestLlmManagerDryRun(unittest.TestCase):
    """Tests for --dry-run: extractor is called, DB is not written."""

    def _make_args(self, dry_run=True, overwrite=False, mode="llm:test-model"):
        args = argparse.Namespace(
            mode=mode,
            db="/tmp/test.db",
            overwrite=overwrite,
            drop=False,
            dry_run=dry_run,
            diff=None,
            debug_dir="debug-output",
            log="WARNING",
            jobs=1,
            batch=None,
            files=[],
        )
        return args

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_dry_run_calls_llm_but_not_store(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_result = MagicMock()
        fake_result.mp.options = [MagicMock(), MagicMock()]
        fake_result.stats = ExtractionStats(elapsed_seconds=0)
        mock_ext = MagicMock()
        mock_ext.extract.return_value = fake_result
        mock_make_ext.return_value = mock_ext

        from explainshell.manager import main

        args = self._make_args(dry_run=True)
        ret = main(args)

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertEqual(ret, 0)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_dry_run_skipped_file_returns_success(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext
    ):
        """Skipped files are not failures — return code should be 0."""
        mock_collect.return_value = ["/fake/echo.1.gz"]
        from explainshell.errors import SkippedExtraction

        mock_ext = MagicMock()
        mock_ext.extract.side_effect = SkippedExtraction("no OPTIONS section")
        mock_make_ext.return_value = mock_ext

        from explainshell.manager import main

        args = self._make_args(dry_run=True)
        ret = main(args)

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertEqual(ret, 0)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_dry_run_failed_file_returns_failure(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext
    ):
        """Failed extraction should cause non-zero return code."""
        mock_collect.return_value = ["/fake/echo.1.gz"]
        from explainshell.errors import ExtractionError

        mock_ext = MagicMock()
        mock_ext.extract.side_effect = ExtractionError("parse error")
        mock_make_ext.return_value = mock_ext

        from explainshell.manager import main

        args = self._make_args(dry_run=True)
        ret = main(args)

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertEqual(ret, 1)

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.manager._collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_normal_run_writes_to_store(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run_seq
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        from explainshell import errors

        mock_store.find_man_page.side_effect = errors.ProgramDoesNotExist("echo")

        fake_mp = MagicMock()
        fake_mp.options = [MagicMock()]
        fake_raw = MagicMock()

        mock_make_ext.return_value = MagicMock()

        def _fake_run_sequential(ext, files, on_start=None, on_result=None):
            from explainshell.extraction.types import BatchResult

            batch = BatchResult()
            for gz_path in files:
                if on_start:
                    on_start(gz_path)
                entry = ExtractionResult(
                    gz_path=gz_path,
                    outcome=ExtractionOutcome.SUCCESS,
                    mp=fake_mp,
                    raw=fake_raw,
                    stats=ExtractionStats(),
                )
                batch.files.append(entry)
                if on_result:
                    on_result(gz_path, entry)
            return batch

        mock_run_seq.side_effect = _fake_run_sequential

        from explainshell.manager import main

        args = self._make_args(dry_run=False)
        main(args)

        mock_store.add_manpage.assert_called_once_with(fake_mp, fake_raw)


# ---------------------------------------------------------------------------
# TestDiffExtractorsFailureHandling
# ---------------------------------------------------------------------------


class TestDiffExtractorsFailureHandling(unittest.TestCase):
    """Tests for _run_diff_extractors under partial/total failure."""

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_partial_failure_preserves_successful_stats(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When one side fails, the successful side's stats are still counted."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        left_batch = BatchResult()
        left_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=100, output_tokens=50),
                mp=MagicMock(),
            ),
            ExtractionResult(
                gz_path="/fake/b.1.gz",
                outcome=ExtractionOutcome.FAILED,
                error="parse error",
            ),
        ]

        right_batch = BatchResult()
        right_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=200, output_tokens=80),
                mp=MagicMock(),
            ),
            ExtractionResult(
                gz_path="/fake/b.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=150, output_tokens=60),
                mp=MagicMock(),
            ),
        ]

        mock_run_seq.side_effect = [left_batch, right_batch]

        from explainshell.manager import _run_diff_extractors

        result = _run_diff_extractors(
            ["/fake/a.1.gz", "/fake/b.1.gz"],
            ("source", None),
            ("mandoc", None),
            None,
        )

        # File a: both OK → 100+200 input tokens
        # File b: left FAILED, right OK → right's 150 tokens preserved
        self.assertEqual(result.stats.input_tokens, 100 + 200 + 150)
        self.assertEqual(result.stats.output_tokens, 50 + 80 + 60)
        self.assertEqual(len(result.succeeded), 1)
        self.assertEqual(len(result.failed), 1)

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_failed_takes_precedence_over_skipped(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When one side is SKIPPED and the other FAILED, outcome is FAILED."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        left_batch = BatchResult()
        left_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="no OPTIONS section",
            ),
        ]

        right_batch = BatchResult()
        right_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.FAILED,
                error="parse error",
            ),
        ]

        mock_run_seq.side_effect = [left_batch, right_batch]

        from explainshell.manager import _run_diff_extractors

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("source", None),
            ("mandoc", None),
            None,
        )

        self.assertEqual(len(result.failed), 1)
        self.assertEqual(len(result.skipped), 0)
        self.assertEqual(result.files[0].outcome, ExtractionOutcome.FAILED)
        self.assertEqual(result.files[0].error, "parse error")

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_both_skipped_yields_skipped_outcome(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When both extractors skip, outcome is SKIPPED (not FAILED)."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        left_batch = BatchResult()
        left_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="no OPTIONS section",
            ),
        ]

        right_batch = BatchResult()
        right_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="too short",
            ),
        ]

        mock_run_seq.side_effect = [left_batch, right_batch]

        from explainshell.manager import _run_diff_extractors

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("source", None),
            ("mandoc", None),
            None,
        )

        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(len(result.failed), 0)
        self.assertEqual(result.files[0].outcome, ExtractionOutcome.SKIPPED)


class TestDiffExtractorLabels(unittest.TestCase):
    """Labels in diff output must include model when present."""

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_llm_vs_llm_labels_include_model(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When both sides are llm:<model>, labels must distinguish them."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        mp = MagicMock()
        mp.options = []

        left_batch = BatchResult()
        left_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=100, output_tokens=50),
                mp=mp,
            ),
        ]

        right_batch = BatchResult()
        right_batch.files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=200, output_tokens=80),
                mp=mp,
            ),
        ]

        mock_run_seq.side_effect = [left_batch, right_batch]

        from explainshell.manager import _run_diff_extractors

        import logging

        with self.assertLogs("explainshell.manager", level=logging.INFO) as cm:
            _run_diff_extractors(
                ["/fake/a.1.gz"],
                ("llm", "openai/gpt-5-mini"),
                ("llm", "gemini/2.5-flash"),
                None,
            )

        log_text = "\n".join(cm.output)
        # Header must show full qualified labels, not bare "llm vs llm"
        self.assertIn("llm (openai/gpt-5-mini) vs llm (gemini/2.5-flash)", log_text)
        # Token lines must distinguish the two models
        self.assertIn("llm (openai/gpt-5-mini)", log_text)
        self.assertIn("llm (gemini/2.5-flash)", log_text)


if __name__ == "__main__":
    unittest.main()
