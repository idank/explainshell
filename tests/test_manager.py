"""Unit tests for explainshell.manager."""

import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from explainshell.extraction.manifest import BatchManifest
from explainshell.extraction.types import (
    BatchResult,
    ExtractionResult,
    ExtractionStats,
    ExtractionOutcome,
)
from explainshell.models import Option, ParsedManpage, RawManpage
from explainshell.store import Store


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
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.return_value = False

        mock_make_ext.return_value = MagicMock()

        # When run() is called, simulate per-file callbacks
        writes_at_callback = []

        def _fake_run(
            ext,
            files,
            batch_size=None,
            jobs=1,
            on_start=None,
            on_result=None,
            manifest=None,
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
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.return_value = False
        mock_make_ext.return_value = MagicMock()

        def _fake_run(
            ext,
            files,
            batch_size=None,
            jobs=1,
            on_start=None,
            on_result=None,
            manifest=None,
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
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.return_value = False

        fake_mp = MagicMock()
        fake_mp.options = [MagicMock()]
        fake_raw = MagicMock()

        mock_make_ext.return_value = MagicMock()

        def _fake_run(
            ext,
            files,
            batch_size=None,
            jobs=1,
            on_start=None,
            on_result=None,
            manifest=None,
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
        mock_store.has_manpage_source.assert_called_once_with("fake/echo.1.gz")
        mock_store.find_man_page.assert_not_called()


# ---------------------------------------------------------------------------
# TestSymlinkMapping
# ---------------------------------------------------------------------------


class TestSymlinkMapping(unittest.TestCase):
    """Verify symlinks are mapped to their canonical manpage instead of extracted."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_mapped_after_extraction(
        self,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
    ):
        """A symlink whose canonical is extracted in the same batch gets mapped."""
        canonical = "/fake/distro/release/1/bio-eagle.1.gz"
        symlink = "/fake/distro/release/1/eagle.1.gz"
        mock_collect.return_value = [canonical, symlink]

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.mapping_score.return_value = None  # no existing mapping

        mock_make_ext.return_value = MagicMock()

        # Track which sources have been "stored" via add_manpage.
        stored: set[str] = set()

        def _has_manpage_source(source: str) -> bool:
            return source in stored

        mock_store.has_manpage_source.side_effect = _has_manpage_source

        def _fake_run(
            ext,
            files,
            batch_size=None,
            jobs=1,
            on_start=None,
            on_result=None,
            manifest=None,
        ):
            batch = BatchResult()
            for gz_path in files:
                if on_start:
                    on_start(gz_path)
                mp = MagicMock(options=[], source="distro/release/1/bio-eagle.1.gz")
                entry = ExtractionResult(
                    gz_path=gz_path,
                    outcome=ExtractionOutcome.SUCCESS,
                    mp=mp,
                    raw=MagicMock(),
                    stats=ExtractionStats(),
                )
                batch.n_succeeded += 1
                # Simulate add_manpage storing the source.
                stored.add(mp.source)
                if on_result:
                    on_result(gz_path, entry)
            return batch

        mock_run.side_effect = _fake_run

        with (
            patch("os.path.islink", side_effect=lambda p: p == symlink),
            patch(
                "os.path.realpath",
                side_effect=lambda p: canonical if p == symlink else p,
            ),
        ):
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

        self.assertEqual(result.exit_code, 0, result.output)
        # Only the canonical should be passed to run(), not the symlink.
        (_, call_files), call_kwargs = mock_run.call_args
        self.assertEqual(call_files, [canonical])
        # Mapping inserted for symlink.
        mock_store.add_mapping.assert_called_once_with(
            "eagle", "distro/release/1/bio-eagle.1.gz", score=10
        )

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_skipped_when_canonical_missing(
        self,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
    ):
        """A symlink whose canonical is not in the DB gets a warning, not a mapping."""
        symlink = "/fake/distro/release/1/eagle.1.gz"
        mock_collect.return_value = [symlink]

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.return_value = False
        mock_store.mapping_score.return_value = None

        mock_make_ext.return_value = MagicMock()
        mock_run.return_value = BatchResult()

        with (
            patch("os.path.islink", return_value=True),
            patch(
                "os.path.realpath", return_value="/fake/distro/release/1/bio-eagle.1.gz"
            ),
        ):
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

        self.assertEqual(result.exit_code, 0, result.output)
        # No mapping should be inserted.
        mock_store.add_mapping.assert_not_called()

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_already_mapped_at_score_10(
        self,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
    ):
        """Re-run: symlink mapping already exists at score 10, no change needed."""
        canonical = "/fake/distro/release/1/bio-eagle.1.gz"
        symlink = "/fake/distro/release/1/eagle.1.gz"
        mock_collect.return_value = [symlink]

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.side_effect = (
            lambda s: s == "distro/release/1/bio-eagle.1.gz"
        )
        mock_store.mapping_score.return_value = 10  # already at score 10

        mock_make_ext.return_value = MagicMock()
        mock_run.return_value = BatchResult()

        with (
            patch("os.path.islink", side_effect=lambda p: p == symlink),
            patch(
                "os.path.realpath",
                side_effect=lambda p: canonical if p == symlink else p,
            ),
        ):
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

        self.assertEqual(result.exit_code, 0, result.output)
        mock_store.add_mapping.assert_not_called()
        mock_store.update_mapping_score.assert_not_called()

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_upgrades_lexgrog_alias_score(
        self,
        mock_collect,
        mock_store_create,
        mock_make_ext,
        mock_run,
    ):
        """A lexgrog alias at score 1 is upgraded to score 10 by symlink mapping."""
        canonical = "/fake/distro/release/1/bio-eagle.1.gz"
        symlink = "/fake/distro/release/1/eagle.1.gz"
        mock_collect.return_value = [symlink]

        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_store.has_manpage_source.side_effect = (
            lambda s: s == "distro/release/1/bio-eagle.1.gz"
        )
        mock_store.mapping_score.return_value = 1  # lexgrog alias at low score

        mock_make_ext.return_value = MagicMock()
        mock_run.return_value = BatchResult()

        with (
            patch("os.path.islink", side_effect=lambda p: p == symlink),
            patch(
                "os.path.realpath",
                side_effect=lambda p: canonical if p == symlink else p,
            ),
        ):
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

        self.assertEqual(result.exit_code, 0, result.output)
        # Score should be upgraded, not a new insert.
        mock_store.add_mapping.assert_not_called()
        mock_store.update_mapping_score.assert_called_once_with(
            "eagle", "distro/release/1/bio-eagle.1.gz", score=10
        )


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
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
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
            [
                "--db",
                "/tmp/test.db",
                "diff",
                "db",
                "--mode",
                "source",
                "--dry-run",
                "/fake/a.1.gz",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        # When dry_run=True, ExtractorConfig receives debug=True and
        # output_dir is set to the run directory.
        from explainshell.extraction import ExtractorConfig

        call_args = mock_make_ext.call_args
        cfg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("cfg")
        if isinstance(cfg, ExtractorConfig):
            self.assertIsNotNone(cfg.run_dir)
            self.assertTrue(cfg.debug)

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


# ---------------------------------------------------------------------------
# TestDbPathValidation
# ---------------------------------------------------------------------------


class TestDbPathValidation(unittest.TestCase):
    """CLI gives clean errors for missing/nonexistent --db."""

    def test_no_db_set(self):
        """Commands that need a DB fail cleanly when --db is not set."""
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["show", "stats"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No database path", result.output)

    def test_nonexistent_db(self):
        """Read-only commands fail cleanly when DB file doesn't exist."""
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", "/tmp/does-not-exist-12345.db", "show", "stats"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Database not found", result.output)

    def test_dry_run_without_db(self):
        """extract --dry-run should not require a DB path."""
        from explainshell.manager import cli

        runner = CliRunner()
        with (
            patch("explainshell.manager.make_extractor") as mock_ext,
            patch("explainshell.util.collect_gz_files", return_value=["/fake/a.1.gz"]),
            patch(
                "explainshell.manager.config.source_from_path",
                return_value="fake/a.1.gz",
            ),
        ):
            fake_result = MagicMock()
            fake_result.outcome = ExtractionOutcome.SUCCESS
            fake_result.mp.options = []
            fake_result.stats = ExtractionStats(elapsed_seconds=0)
            mock_ext.return_value = MagicMock()
            mock_ext.return_value.extract.return_value = fake_result

            result = runner.invoke(
                cli,
                ["extract", "--mode", "source", "--dry-run", "/fake/a.1.gz"],
            )

        self.assertEqual(result.exit_code, 0)


# ---------------------------------------------------------------------------
# TestShowCli — uses real temp DB
# ---------------------------------------------------------------------------


def _make_raw() -> RawManpage:
    return RawManpage(
        source_text="test manpage content",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )


def _make_manpage(
    name: str,
    section: str = "1",
    distro: str = "ubuntu",
    release: str = "25.10",
    aliases: list[tuple[str, int]] | None = None,
    options: list[Option] | None = None,
) -> ParsedManpage:
    source = f"{distro}/{release}/{section}/{name}.{section}.gz"
    if aliases is None:
        aliases = [(name, 10)]
    return ParsedManpage(
        source=source,
        name=name,
        synopsis=f"{name} - do things",
        aliases=aliases,
        options=options or [],
        extractor="source",
    )


class TestShowCli(unittest.TestCase):
    """CliRunner tests for the ``show`` command group."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        self.store = Store.create(self.db_path)
        self.store.add_manpage(
            _make_manpage(
                "tar",
                options=[
                    Option(text="create archive", short=["-c"], long=["--create"]),
                    Option(text="extract", short=["-x"], long=["--extract"]),
                ],
            ),
            _make_raw(),
        )
        self.store.add_manpage(_make_manpage("echo"), _make_raw())

    def tearDown(self):
        self.store.close()
        os.unlink(self.db_path)
        os.rmdir(self.tmp)

    def test_show_stats(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "stats"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("parsed_manpages:   2", result.output)
        self.assertIn("ubuntu/25.10", result.output)

    def test_show_distros(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "distros"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("ubuntu/25.10", result.output)

    def test_show_manpage(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "manpage", "tar"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("name: tar", result.output)
        self.assertIn("options: 2", result.output)
        self.assertIn("--create", result.output)

    def test_show_manpage_not_found(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "manpage", "nonexistent"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Not found", result.output)

    def test_show_sections(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "sections", "ubuntu", "25.10"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("1", result.output)

    def test_show_manpages(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "manpages", "ubuntu/25.10/1/"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("tar.1.gz", result.output)
        self.assertIn("echo.1.gz", result.output)

    def test_show_mappings(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "mappings"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("tar ->", result.output)
        self.assertIn("echo ->", result.output)


# ---------------------------------------------------------------------------
# TestDbCheckCli
# ---------------------------------------------------------------------------


class TestDbCheckCli(unittest.TestCase):
    """CliRunner tests for the ``db-check`` command."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        self.store = Store.create(self.db_path)

    def tearDown(self):
        self.store.close()
        os.unlink(self.db_path)
        os.rmdir(self.tmp)

    def test_clean_db(self):
        self.store.add_manpage(_make_manpage("tar"), _make_raw())

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "db-check"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No issues found", result.output)

    def test_reports_issues(self):
        """Insert an orphaned mapping so db-check has something to report."""
        self.store._conn.execute("PRAGMA foreign_keys = OFF")
        self.store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("ghost", "ubuntu/25.10/1/ghost.1.gz", 10),
        )
        self.store._conn.commit()
        self.store._conn.execute("PRAGMA foreign_keys = ON")

        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "db-check"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("orphaned mapping", result.output)

    def test_nonexistent_db(self):
        from explainshell.manager import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", "/tmp/does-not-exist-12345.db", "db-check"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Database not found", result.output)


# ---------------------------------------------------------------------------
# TestAtFileExpansion
# ---------------------------------------------------------------------------


class TestAtFileExpansion(unittest.TestCase):
    """Tests that @file arguments are expanded through the CLI."""

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_extract_expands_at_file(
        self,
        mock_source: MagicMock,
        mock_store_create: MagicMock,
        mock_make_ext: MagicMock,
    ) -> None:
        """@file arg is expanded to the file's contents and passed to extraction."""
        fake_result = MagicMock()
        fake_result.outcome = ExtractionOutcome.SUCCESS
        fake_result.mp.options = [MagicMock()]
        fake_result.stats = ExtractionStats(elapsed_seconds=0)
        mock_ext = MagicMock()
        mock_ext.extract.return_value = fake_result
        mock_make_ext.return_value = mock_ext

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n")
            f.flush()
            list_path = f.name

        try:
            from explainshell.manager import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["extract", "--mode", "llm:test-model", "--dry-run", f"@{list_path}"],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        finally:
            os.unlink(list_path)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_extract_at_file_skips_blanks_and_comments(
        self,
        mock_source: MagicMock,
        mock_store_create: MagicMock,
        mock_make_ext: MagicMock,
    ) -> None:
        fake_result = MagicMock()
        fake_result.outcome = ExtractionOutcome.SUCCESS
        fake_result.mp.options = [MagicMock()]
        fake_result.stats = ExtractionStats(elapsed_seconds=0)
        mock_ext = MagicMock()
        mock_ext.extract.return_value = fake_result
        mock_make_ext.return_value = mock_ext

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n\n# comment\n  \n")
            f.flush()
            list_path = f.name

        try:
            from explainshell.manager import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["extract", "--mode", "llm:test-model", "--dry-run", f"@{list_path}"],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            mock_ext.extract.assert_called_once_with("/fake/echo.1.gz")
        finally:
            os.unlink(list_path)

    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch(
        "explainshell.manager.config.source_from_path", return_value="fake/echo.1.gz"
    )
    def test_extract_mixed_plain_and_at_file(
        self,
        mock_source: MagicMock,
        mock_store_create: MagicMock,
        mock_make_ext: MagicMock,
    ) -> None:
        fake_result = MagicMock()
        fake_result.outcome = ExtractionOutcome.SUCCESS
        fake_result.mp.options = [MagicMock()]
        fake_result.stats = ExtractionStats(elapsed_seconds=0)
        mock_ext = MagicMock()
        mock_ext.extract.return_value = fake_result
        mock_make_ext.return_value = mock_ext

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n")
            f.flush()
            list_path = f.name

        try:
            from explainshell.manager import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "extract",
                    "--mode",
                    "llm:test-model",
                    "--dry-run",
                    "/fake/other.1.gz",
                    f"@{list_path}",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            calls = [c.args[0] for c in mock_ext.extract.call_args_list]
            self.assertIn("/fake/other.1.gz", calls)
            self.assertIn("/fake/echo.1.gz", calls)
        finally:
            os.unlink(list_path)


# ---------------------------------------------------------------------------
# TestRunSalvage
# ---------------------------------------------------------------------------


class TestRunSalvage(unittest.TestCase):
    """Tests for the manifest-based _run_salvage function."""

    def _make_manifest_data(
        self,
        batches: list[dict],
        model: str = "openai/gpt-5-mini",
    ) -> BatchManifest:
        return BatchManifest(
            version=1,
            model=model,
            batch_size=50,
            total_batches=len(batches),
            batches=batches,
        )

    def test_no_failed_batches(self) -> None:
        """When all batches completed, _run_salvage returns empty result."""
        from explainshell.manager import _run_salvage

        manifest_data = self._make_manifest_data(
            [
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "completed",
                    "error": None,
                    "files": ["/fake/a.gz"],
                },
            ]
        )

        ext = MagicMock()
        result = _run_salvage(ext, manifest_data, s=None, dry_run=True)

        self.assertEqual(result.n_succeeded, 0)
        self.assertEqual(result.n_failed, 0)

    def test_salvage_failed_batch(self) -> None:
        """Failed batch files are re-prepared, finalized, and counted as succeeded."""
        from explainshell.extraction.llm.providers import BatchResults, TokenUsage
        from explainshell.manager import _run_salvage

        manifest_data = self._make_manifest_data(
            [
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "failed",
                    "error": "expired",
                    "files": ["/fake/a.gz"],
                },
            ]
        )

        ext = MagicMock()
        job = MagicMock()
        ext.batch_provider.retrieve_batch.return_value = job
        ext.batch_provider.collect_results.return_value = BatchResults(
            {"0:0": '{"options":[]}'}, TokenUsage(100, 50)
        )
        fake_mp = MagicMock(options=[MagicMock()])
        fake_entry = ExtractionResult(
            gz_path="/fake/a.gz",
            mp=fake_mp,
            raw=MagicMock(),
            stats=ExtractionStats(chunks=1, plain_text_len=100),
        )
        ext.finalize.return_value = fake_entry

        prepared = MagicMock()
        prepared.n_chunks = 1
        ext.prepare.return_value = prepared

        result = _run_salvage(ext, manifest_data, s=None, dry_run=True)

        self.assertEqual(result.n_succeeded, 1)
        self.assertEqual(result.n_failed, 0)
        # Failed batches are already terminal — retrieve, don't poll.
        ext.batch_provider.retrieve_batch.assert_called_once_with("b1")
        ext.batch_provider.poll_batch.assert_not_called()
        ext.prepare.assert_called_once_with("/fake/a.gz")

    def test_null_batch_id_skipped(self) -> None:
        """Batches with null batch_id (submit failed) are skipped, files counted as failed."""
        from explainshell.manager import _run_salvage

        manifest_data = self._make_manifest_data(
            [
                {
                    "batch_idx": 1,
                    "batch_id": None,
                    "status": "failed",
                    "error": "submit failed",
                    "files": ["/fake/a.gz", "/fake/b.gz"],
                },
            ]
        )

        ext = MagicMock()
        result = _run_salvage(ext, manifest_data, s=None, dry_run=True)

        self.assertEqual(result.n_failed, 2)
        self.assertEqual(result.n_succeeded, 0)
        ext.batch_provider.retrieve_batch.assert_not_called()

    def test_submitted_status_polls_before_collecting(self) -> None:
        """Batches in 'submitted' status are polled to terminal state, not just retrieved."""
        from explainshell.extraction.llm.providers import BatchResults, TokenUsage
        from explainshell.manager import _run_salvage

        manifest_data = self._make_manifest_data(
            [
                {
                    "batch_idx": 1,
                    "batch_id": "b1",
                    "status": "submitted",
                    "error": None,
                    "files": ["/fake/a.gz"],
                },
            ]
        )

        ext = MagicMock()
        poll_client = MagicMock()
        ext.batch_provider.make_poll_client.return_value = poll_client
        polled_job = MagicMock()
        ext.batch_provider.poll_batch.return_value = polled_job
        ext.batch_provider.collect_results.return_value = BatchResults(
            {"0:0": '{"options":[]}'}, TokenUsage(100, 50)
        )
        fake_mp = MagicMock(options=[MagicMock()])
        fake_entry = ExtractionResult(
            gz_path="/fake/a.gz",
            mp=fake_mp,
            raw=MagicMock(),
            stats=ExtractionStats(chunks=1, plain_text_len=100),
        )
        ext.finalize.return_value = fake_entry

        prepared = MagicMock()
        prepared.n_chunks = 1
        ext.prepare.return_value = prepared

        result = _run_salvage(ext, manifest_data, s=None, dry_run=True)

        self.assertEqual(result.n_succeeded, 1)
        # Must poll, not just retrieve.
        ext.batch_provider.make_poll_client.assert_called_once()
        ext.batch_provider.poll_batch.assert_called_once_with(
            poll_client, "b1", poll_interval=30, stop_event=None
        )
        ext.batch_provider.retrieve_batch.assert_not_called()
        # collect_results called with the polled job, not a retrieved one.
        ext.batch_provider.collect_results.assert_called_once_with(polled_job)


class TestSalvageCliValidation(unittest.TestCase):
    """CLI-level validation for the salvage command."""

    def test_manifest_model_mismatch_fails(self) -> None:
        """salvage rejects a manifest whose model differs from --mode."""
        manifest_data = {
            "version": 1,
            "model": "openai/gpt-5-mini",
            "batch_size": 50,
            "total_batches": 0,
            "batches": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(manifest_data, f)
            manifest_path = f.name

        try:
            from explainshell.manager import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "salvage",
                    "--mode",
                    "llm:openai/gpt-4o",
                    "--manifest",
                    manifest_path,
                    "--dry-run",
                ],
            )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("does not match", result.output)
        finally:
            os.unlink(manifest_path)

    def test_manifest_bad_version_fails(self) -> None:
        """salvage rejects a manifest with unsupported version."""
        manifest_data = {
            "version": 999,
            "model": "openai/gpt-5-mini",
            "batch_size": 50,
            "total_batches": 0,
            "batches": [],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(manifest_data, f)
            manifest_path = f.name

        try:
            from explainshell.manager import cli

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "salvage",
                    "--mode",
                    "llm:openai/gpt-5-mini",
                    "--manifest",
                    manifest_path,
                    "--dry-run",
                ],
            )

            self.assertNotEqual(result.exit_code, 0)
            # Pydantic rejects version=999 since only 1 is allowed.
            self.assertIn("version", result.output)
        finally:
            os.unlink(manifest_path)


# ---------------------------------------------------------------------------
# TestExtractionReport
# ---------------------------------------------------------------------------


class TestExtractionReport(unittest.TestCase):
    """Verify _write_report produces correct report.json files."""

    def setUp(self) -> None:
        self._run_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._run_dir, ignore_errors=True)

    def _read_report(self) -> dict:
        path = os.path.join(self._run_dir, "report.json")
        self.assertTrue(
            os.path.isfile(path), f"report.json not found in {self._run_dir}"
        )
        with open(path) as f:
            return json.load(f)

    def _make_report(self, **overrides):
        from explainshell.extraction.report import (
            DbCounts,
            ExtractConfig,
            ExtractSummary,
            ExtractionReport,
            GitInfo,
        )

        defaults = dict(
            timestamp="2026-03-30T12:00:00+00:00",
            git=GitInfo(commit="abc123", commit_short="abc123", dirty=False),
            config=ExtractConfig(mode="llm", model="openai/test-model"),
            elapsed_seconds=1.0,
            summary=ExtractSummary(succeeded=1, skipped=0, failed=0),
            db_before=DbCounts(manpages=10, mappings=50),
            db_after=DbCounts(manpages=11, mappings=55),
        )
        defaults.update(overrides)
        return ExtractionReport(**defaults)

    def test_report_schema(self) -> None:
        """report.json has the expected top-level fields and values."""
        from explainshell.manager import _write_report

        report = self._make_report()
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["command"], "extract")
        self.assertEqual(data["timestamp"], "2026-03-30T12:00:00+00:00")
        self.assertEqual(data["config"]["mode"], "llm")
        self.assertEqual(data["config"]["model"], "openai/test-model")
        self.assertEqual(data["elapsed_seconds"], 1.0)
        self.assertEqual(data["summary"]["succeeded"], 1)
        self.assertEqual(data["summary"]["failed"], 0)
        self.assertEqual(data["db_before"], {"manpages": 10, "mappings": 50})
        self.assertEqual(data["db_after"], {"manpages": 11, "mappings": 55})

    def test_none_fields_excluded(self) -> None:
        """Fields set to None are omitted from the JSON (exclude_none)."""
        from explainshell.manager import _write_report

        report = self._make_report(batch_manifest=None)
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertNotIn("batch_manifest", data)

    def test_batch_manifest_embedded(self) -> None:
        """batch_manifest dict is included when provided."""
        from explainshell.manager import _write_report

        manifest_dict = {
            "version": 1,
            "model": "openai/test-model",
            "batch_size": 50,
            "total_batches": 1,
            "batches": [],
        }
        report = self._make_report(batch_manifest=manifest_dict)
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertEqual(data["batch_manifest"]["model"], "openai/test-model")
        self.assertEqual(data["batch_manifest"]["batch_size"], 50)

    def test_standalone_manifest_cleaned_up(self) -> None:
        """_write_report removes batch-manifest.json if it exists."""
        from explainshell.manager import _write_report

        standalone = os.path.join(self._run_dir, "batch-manifest.json")
        with open(standalone, "w") as f:
            f.write("{}")

        report = self._make_report(batch_manifest={"version": 1})
        _write_report(self._run_dir, report)

        self.assertFalse(os.path.isfile(standalone))

    def test_interrupted_report(self) -> None:
        """Interrupted runs record interrupted=true in the summary."""
        from explainshell.extraction.report import ExtractSummary
        from explainshell.manager import _write_report

        report = self._make_report(
            summary=ExtractSummary(succeeded=0, skipped=0, failed=0, interrupted=True),
        )
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertTrue(data["summary"]["interrupted"])

    def test_fatal_error_report(self) -> None:
        """Fatal errors are recorded in summary.fatal_error."""
        from explainshell.extraction.report import ExtractSummary
        from explainshell.manager import _write_report

        report = self._make_report(
            summary=ExtractSummary(
                succeeded=0,
                skipped=0,
                failed=1,
                fatal_error="provider auth failed",
            ),
        )
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertEqual(data["summary"]["fatal_error"], "provider auth failed")
        self.assertEqual(data["summary"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
