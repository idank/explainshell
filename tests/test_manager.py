"""Unit tests for explainshell.manager."""

import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from explainshell.extraction.types import (
    ExtractionResult,
    ExtractionStats,
    ExtractionOutcome,
)


# ---------------------------------------------------------------------------
# TestBatchPerBatchDbWrites
# ---------------------------------------------------------------------------


class TestBatchPerBatchDbWrites(unittest.TestCase):
    """Verify the manager writes results to the DB via on_result callback
    from run()."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_db_writes_after_each_batch(
        self,
        mock_source,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
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

        # When run() is called, simulate per-file callbacks
        writes_at_callback = []

        def _fake_run(
            ext, files, batch_size=None, jobs=1, on_start=None, on_result=None
        ):
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
                batch.n_succeeded += 1
                if on_result:
                    writes_at_callback.append(mock_store.add_manpage.call_count)
                    on_result(gz_path, entry)
            return batch

        mock_run.side_effect = _fake_run

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "extract",
                "--mode",
                "llm:openai/test-model",
                "--batch",
                "2",
                "/fake/file.gz",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        # on_result is called 4 times (once per file), and each call writes to DB
        self.assertEqual(mock_store.add_manpage.call_count, 4)
        # Writes are incremental: 0 before first, 1 before second, etc.
        self.assertEqual(writes_at_callback, [0, 1, 2, 3])

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_batch2_failure_preserves_batch1_writes(
        self,
        mock_source,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
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

        def _fake_run(
            ext, files, batch_size=None, jobs=1, on_start=None, on_result=None
        ):
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
                if entry.outcome == ExtractionOutcome.SUCCESS:
                    batch.n_succeeded += 1
                else:
                    batch.n_failed += 1
                if on_result:
                    on_result(gz_path, entry)
            return batch

        mock_run.side_effect = _fake_run

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "extract",
                "--mode",
                "llm:openai/test-model",
                "--batch",
                "2",
                "/fake/file.gz",
            ],
        )

        # Only 2 successful files were written
        self.assertEqual(mock_store.add_manpage.call_count, 2)
        # Return code is non-zero because some files failed
        self.assertNotEqual(result.exit_code, 0)


# ---------------------------------------------------------------------------
# TestLlmManagerDryRun
# ---------------------------------------------------------------------------


class TestLlmManagerDryRun(unittest.TestCase):
    """Tests for --dry-run: extractor is called, DB is not written."""

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_dry_run_calls_llm_but_not_store(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_result = MagicMock()
        fake_result.outcome = ExtractionOutcome.SUCCESS
        fake_result.mp.options = [MagicMock(), MagicMock()]
        fake_result.stats = ExtractionStats(elapsed_seconds=0)
        mock_ext = MagicMock()
        mock_ext.extract.return_value = fake_result
        mock_make_ext.return_value = mock_ext

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["extract", "--mode", "llm:test-model", "--dry-run", "/fake/echo.1.gz"],
        )

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertEqual(result.exit_code, 0)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
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

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["extract", "--mode", "llm:test-model", "--dry-run", "/fake/echo.1.gz"],
        )

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertEqual(result.exit_code, 0)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
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

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["extract", "--mode", "llm:test-model", "--dry-run", "/fake/echo.1.gz"],
        )

        mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        mock_store_create.assert_not_called()
        self.assertNotEqual(result.exit_code, 0)

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_normal_run_writes_to_store(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run
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

        def _fake_run(
            ext, files, batch_size=None, jobs=1, on_start=None, on_result=None
        ):
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
                batch.n_succeeded += 1
                if on_result:
                    on_result(gz_path, entry)
            return batch

        mock_run.side_effect = _fake_run

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "extract",
                "--mode",
                "llm:test-model",
                "/fake/echo.1.gz",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_store.add_manpage.assert_called_once_with(fake_mp, fake_raw)


# ---------------------------------------------------------------------------
# TestDiffDbCli
# ---------------------------------------------------------------------------


class TestDiffDbCli(unittest.TestCase):
    """CliRunner tests for the ``diff db`` command surface."""

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_db_success(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run_seq
    ):
        """Basic diff db invocation succeeds."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_make_ext.return_value = MagicMock()

        from explainshell.extraction.types import BatchResult

        mock_run_seq.return_value = BatchResult()

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--db", "/tmp/test.db", "diff", "db", "--mode", "source", "/fake/a.1.gz"],
        )

        self.assertEqual(result.exit_code, 0)
        mock_store_create.assert_called_once_with("/tmp/test.db")
        mock_make_ext.assert_called_once()

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_db_dry_run_threads_through(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run_seq
    ):
        """--dry-run is forwarded to _run_diff_db."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_store_create.return_value = MagicMock()
        mock_make_ext.return_value = MagicMock()

        from explainshell.extraction.types import BatchResult

        mock_run_seq.return_value = BatchResult()

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["diff", "db", "--mode", "source", "--dry-run", "/fake/a.1.gz"],
        )

        self.assertEqual(result.exit_code, 0)
        # When dry_run=True, ExtractorConfig receives debug_dir=debug_dir
        # (the default "debug-output") instead of None.
        from explainshell.extraction import ExtractorConfig

        call_args = mock_make_ext.call_args
        cfg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("cfg")
        if isinstance(cfg, ExtractorConfig):
            self.assertEqual(cfg.debug_dir, "debug-output")

    def test_diff_db_invalid_mode(self):
        """Invalid mode is rejected."""
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "db", "--mode", "bogus", "/fake/a.1.gz"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid mode", result.output)


# ---------------------------------------------------------------------------
# TestDiffExtractorsCli
# ---------------------------------------------------------------------------


class TestDiffExtractorsCli(unittest.TestCase):
    """CliRunner tests for the ``diff extractors`` command surface."""

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_extractors_success(
        self, mock_source, mock_collect, mock_make_ext, mock_run_seq
    ):
        """Basic diff extractors invocation succeeds."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_make_ext.return_value = MagicMock()

        from explainshell.extraction.types import BatchResult

        mock_run_seq.return_value = BatchResult()

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["diff", "extractors", "source..mandoc", "/fake/a.1.gz"],
        )

        self.assertEqual(result.exit_code, 0)
        # Two extractors should be created (left and right).
        self.assertEqual(mock_make_ext.call_count, 2)

    def test_diff_extractors_invalid_spec_no_dots(self):
        """Spec without '..' is rejected."""
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff", "extractors", "source-mandoc", "/fake/a.1.gz"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid spec", result.output)

    def test_diff_extractors_invalid_mode_in_spec(self):
        """Invalid mode inside A..B spec is rejected."""
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff", "extractors", "source..bogus", "/fake/a.1.gz"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid mode", result.output)


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

        left_files = [
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

        right_files = [
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

        def _fake_run_seq(ext, gz_files, **kwargs):
            files = left_files if mock_run_seq.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run_seq.side_effect = _fake_run_seq

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
        self.assertEqual(result.n_succeeded, 1)
        self.assertEqual(result.n_failed, 1)

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_failed_takes_precedence_over_skipped(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When one side is SKIPPED and the other FAILED, outcome is FAILED."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        left_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="no OPTIONS section",
            ),
        ]

        right_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.FAILED,
                error="parse error",
            ),
        ]

        def _fake_run_seq(ext, gz_files, **kwargs):
            files = left_files if mock_run_seq.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run_seq.side_effect = _fake_run_seq

        from explainshell.manager import _run_diff_extractors

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("source", None),
            ("mandoc", None),
            None,
        )

        self.assertEqual(result.n_failed, 1)
        self.assertEqual(result.n_skipped, 0)

    @patch("explainshell.manager.run_sequential")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_both_skipped_yields_skipped_outcome(
        self, mock_source, mock_make_ext, mock_run_seq
    ):
        """When both extractors skip, outcome is SKIPPED (not FAILED)."""
        mock_source.side_effect = lambda p: p.split("/")[-1]
        from explainshell.extraction.types import BatchResult

        left_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="no OPTIONS section",
            ),
        ]

        right_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SKIPPED,
                error="too short",
            ),
        ]

        def _fake_run_seq(ext, gz_files, **kwargs):
            files = left_files if mock_run_seq.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run_seq.side_effect = _fake_run_seq

        from explainshell.manager import _run_diff_extractors

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("source", None),
            ("mandoc", None),
            None,
        )

        self.assertEqual(result.n_skipped, 1)
        self.assertEqual(result.n_failed, 0)


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

        left_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=100, output_tokens=50),
                mp=mp,
            ),
        ]

        right_files = [
            ExtractionResult(
                gz_path="/fake/a.1.gz",
                outcome=ExtractionOutcome.SUCCESS,
                stats=ExtractionStats(input_tokens=200, output_tokens=80),
                mp=mp,
            ),
        ]

        def _fake_run_seq(ext, gz_files, **kwargs):
            files = left_files if mock_run_seq.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run_seq.side_effect = _fake_run_seq

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


# ---------------------------------------------------------------------------
# TestDiffDbSourceMatch
# ---------------------------------------------------------------------------


class TestDiffDbSourceMatch(unittest.TestCase):
    """Tests for _run_diff_db preferring exact source path over name lookup."""

    def _run_diff_db_with_store(
        self, gz_path: str, short_path: str, store_mock: MagicMock
    ) -> list[str]:
        """Run _run_diff_db and return captured log lines."""
        from explainshell.manager import _run_diff_db
        from explainshell.extraction.types import BatchResult

        fake_mp = MagicMock()
        fake_mp.options = []

        entry = ExtractionResult(
            gz_path=gz_path,
            outcome=ExtractionOutcome.SUCCESS,
            mp=fake_mp,
            raw=MagicMock(),
            stats=ExtractionStats(),
        )

        with (
            patch("explainshell.manager.make_extractor") as mock_ext,
            patch(
                "explainshell.manager.config.source_from_path",
                return_value=short_path,
            ),
            patch("explainshell.manager.run_sequential") as mock_run,
        ):
            mock_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                on_result = kwargs.get("on_result")
                if on_result:
                    on_result(gz_path, entry)
                return BatchResult()

            mock_run.side_effect = _fake_run

            import logging

            with self.assertLogs("explainshell.manager", level=logging.INFO) as cm:
                _run_diff_db([gz_path], "source", None, None, False, store_mock)

        return cm.output

    def test_exact_source_match_preferred(self):
        """When the exact source path exists in DB, use it directly."""
        store_mock = MagicMock()
        stored_mp = MagicMock()
        stored_mp.options = []
        # First call with short_path (ending in .gz) succeeds.
        store_mock.find_man_page.return_value = [stored_mp]

        self._run_diff_db_with_store(
            "/manpages/ubuntu/26.04/1/find.1.gz",
            "ubuntu/26.04/1/find.1.gz",
            store_mock,
        )

        # Should be called with the full source path first.
        store_mock.find_man_page.assert_called_once_with("ubuntu/26.04/1/find.1.gz")

    def test_falls_back_to_name_when_source_not_found(self):
        """When exact source is not in DB, fall back to name lookup."""
        from explainshell import errors

        store_mock = MagicMock()
        stored_mp = MagicMock()
        stored_mp.options = []

        # First call (source path) raises, second call (name) succeeds.
        store_mock.find_man_page.side_effect = [
            errors.ProgramDoesNotExist("ubuntu/26.04/1/find.1.gz"),
            [stored_mp],
        ]

        self._run_diff_db_with_store(
            "/manpages/ubuntu/26.04/1/find.1.gz",
            "ubuntu/26.04/1/find.1.gz",
            store_mock,
        )

        self.assertEqual(store_mock.find_man_page.call_count, 2)
        calls = store_mock.find_man_page.call_args_list
        self.assertEqual(calls[0].args[0], "ubuntu/26.04/1/find.1.gz")
        self.assertEqual(calls[1].args[0], "find")

    def test_both_lookups_fail_logs_not_in_db(self):
        """When neither source nor name is in DB, log 'not in DB'."""
        from explainshell import errors

        store_mock = MagicMock()
        store_mock.find_man_page.side_effect = errors.ProgramDoesNotExist("x")

        logs = self._run_diff_db_with_store(
            "/manpages/ubuntu/26.04/1/find.1.gz",
            "ubuntu/26.04/1/find.1.gz",
            store_mock,
        )

        log_text = "\n".join(logs)
        self.assertIn("not in DB", log_text)


if __name__ == "__main__":
    unittest.main()
