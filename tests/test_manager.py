"""Unit tests for explainshell.manager."""

import contextlib
import datetime
import json
import logging
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from explainshell.extraction import ExtractorConfig
from explainshell.extraction.report import (
    DbCounts,
    ExtractConfig,
    ExtractionReport,
    ExtractSummary,
    GitInfo,
)
from explainshell.extraction.types import (
    BatchResult,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionStats,
)
from explainshell.manager import (
    _run_diff_db,
    _run_diff_extractors,
    _write_report,
    cli,
)
from explainshell.models import ExtractionMeta, Option, ParsedManpage, RawManpage
from explainshell.store import Store
from explainshell.util import collect_gz_files, name_section

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_raw(sha256: str | None = None) -> RawManpage:
    return RawManpage(
        source_text="test manpage content",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
        source_gz_sha256=sha256,
    )


def _make_manpage(
    name: str,
    section: str = "1",
    distro: str = "ubuntu",
    release: str = "26.04",
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
        extractor="llm",
    )


@contextlib.contextmanager
def _temp_db():
    """Yield a path to a fresh temp SQLite file, cleaned up on exit."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        os.unlink(path)


def _make_manpage_from_source(source: str) -> ParsedManpage:
    """Create a ParsedManpage from a source path like 'distro/release/1/name.1.gz'."""
    basename = os.path.basename(source)  # e.g. "name.1.gz"
    name_with_section = basename[:-3]  # strip ".gz" -> "name.1"

    name, section = name_section(name_with_section)
    parts = source.split("/")
    distro = parts[0] if len(parts) >= 4 else "distro"
    release = parts[1] if len(parts) >= 4 else "release"
    return _make_manpage(name, section=section, distro=distro, release=release)


# ---------------------------------------------------------------------------
# TestBatchPerBatchDbWrites
# ---------------------------------------------------------------------------


class TestBatchPerBatchDbWrites(unittest.TestCase):
    """Verify the manager writes results to the DB via on_result callback
    from run()."""

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_db_writes_after_each_batch(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Verify on_result callback writes to DB for each successful file."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/alpha.1.gz",
                "/fake/distro/release/1/bravo.1.gz",
                "/fake/distro/release/1/charlie.1.gz",
                "/fake/distro/release/1/delta.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            mock_make_ext.return_value = MagicMock()

            # When run() is called, simulate per-file callbacks
            writes_at_callback: list[int] = []

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
                    source = mock_source(gz_path)
                    mp = _make_manpage_from_source(source)
                    raw = _make_raw(sha256=gz_path)
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
                    if on_result:
                        writes_at_callback.append(
                            Store(db_path, read_only=True).counts()["manpages"]
                        )
                        on_result(gz_path, entry)
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "--batch",
                    "2",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0)
            result_store = Store(db_path, read_only=True)
            # on_result is called 4 times (once per file), and each call writes to DB
            self.assertEqual(result_store.counts()["manpages"], 4)
            # Writes are incremental: 0 before first, 1 before second, etc.
            self.assertEqual(writes_at_callback, [0, 1, 2, 3])

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_batch2_failure_preserves_batch1_writes(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """If some files fail, successful files must still be in the DB."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/alpha.1.gz",
                "/fake/distro/release/1/bravo.1.gz",
                "/fake/distro/release/1/charlie.1.gz",
                "/fake/distro/release/1/delta.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

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
                batch = BatchResult()
                for i, gz_path in enumerate(files):
                    if on_start:
                        on_start(gz_path)
                    if i < 2:
                        # First 2 files succeed
                        source = mock_source(gz_path)
                        mp = _make_manpage_from_source(source)
                        raw = _make_raw(sha256=gz_path)
                        entry = ExtractionResult(
                            gz_path=gz_path,
                            outcome=ExtractionOutcome.SUCCESS,
                            mp=mp,
                            raw=raw,
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

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "--batch",
                    "2",
                    "/fake/file.gz",
                ],
            )

            result_store = Store(db_path, read_only=True)
            # Only 2 successful files were written
            self.assertEqual(result_store.counts()["manpages"], 2)
            # Return code is non-zero because some files failed
            self.assertNotEqual(result.exit_code, 0)


# ---------------------------------------------------------------------------
# TestLlmManagerDryRun
# ---------------------------------------------------------------------------


class TestLlmManagerDryRun(unittest.TestCase):
    """Tests for --dry-run: classifier runs, no extraction, no DB writes."""

    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    @patch("os.path.islink", return_value=False)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path",
        return_value="fake/release/1/echo.1.gz",
    )
    def test_dry_run_classifies_without_extracting(
        self,
        _mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_link,
        _mock_sha,
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        with _temp_db() as db_path:
            Store.create(db_path).close()
            runner = CliRunner()
            # Local override of the conftest autouse patch so we can inspect calls.
            with (
                patch("explainshell.manager._attach_run_log") as mock_attach,
                self.assertLogs("explainshell.manager", level="INFO") as cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
                        "extract",
                        "--mode",
                        "llm:test-model",
                        "--dry-run",
                        "/fake/echo.1.gz",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        mock_make_ext.assert_not_called()
        mock_run.assert_not_called()
        mock_attach.assert_not_called()
        msgs = "\n".join(cm.output)
        self.assertIn("WORK", msgs)
        self.assertIn("fake/release/1/echo.1.gz", msgs)
        self.assertIn("plan:", msgs)

    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    @patch("os.path.islink", return_value=False)
    @patch("explainshell.util.collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path",
        return_value="fake/release/1/echo.1.gz",
    )
    def test_dry_run_reports_already_stored(
        self, _mock_source, mock_collect, _mock_link, _mock_sha
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        with _temp_db() as db_path:
            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage("echo", distro="fake", release="release"), _make_raw()
            )
            pre.close()

            runner = CliRunner()
            with self.assertLogs("explainshell.manager", level="INFO") as cm:
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
                        "extract",
                        "--mode",
                        "llm:test-model",
                        "--dry-run",
                        "/fake/echo.1.gz",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        msgs = "\n".join(cm.output)
        self.assertIn("ALREADY", msgs)
        self.assertIn("fake/release/1/echo.1.gz", msgs)

    def test_dry_run_requires_existing_db(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            missing_db = os.path.join(tmp, "absent.db")
            result = runner.invoke(
                cli,
                [
                    "--db",
                    missing_db,
                    "extract",
                    "--mode",
                    "llm:test-model",
                    "--dry-run",
                    "/fake/a.1.gz",
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Database not found", result.output)

    @patch("explainshell.util.collect_gz_files")
    def test_reason_required_for_bulk_extraction(self, mock_collect):
        mock_collect.return_value = [f"/fake/page-{i}.1.gz" for i in range(101)]
        with _temp_db() as db_path:
            Store.create(db_path).close()
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:test-model",
                    *[f"/fake/page-{i}.1.gz" for i in range(101)],
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--reason is required", result.output)
        self.assertIn("got 101", result.output)

    @patch("explainshell.util.collect_gz_files")
    def test_reason_gate_skipped_for_dry_run(self, mock_collect):
        mock_collect.return_value = [f"/fake/page-{i}.1.gz" for i in range(101)]
        with _temp_db() as db_path:
            Store.create(db_path).close()
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:test-model",
                    "--dry-run",
                    *[f"/fake/page-{i}.1.gz" for i in range(101)],
                ],
            )
        self.assertNotIn("--reason is required", result.output)

    @patch("explainshell.util.collect_gz_files")
    def test_reason_gate_skipped_under_threshold(self, mock_collect):
        mock_collect.return_value = [f"/fake/page-{i}.1.gz" for i in range(100)]
        with _temp_db() as db_path:
            Store.create(db_path).close()
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:test-model",
                    *[f"/fake/page-{i}.1.gz" for i in range(100)],
                ],
            )
        self.assertNotIn("--reason is required", result.output)

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch(
        "explainshell.manager.config.source_from_path",
        return_value="fake/release/1/echo.1.gz",
    )
    def test_normal_run_writes_to_store(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        with _temp_db() as db_path:
            mock_collect.return_value = ["/fake/echo.1.gz"]

            fake_mp = _make_manpage("echo", distro="fake", release="release")
            fake_raw = _make_raw(sha256="/fake/echo.1.gz")

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

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:test-model",
                    "/fake/echo.1.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0)
            result_store = Store(db_path, read_only=True)
            self.assertTrue(result_store.has_manpage_source("fake/release/1/echo.1.gz"))
            self.assertEqual(result_store.counts()["manpages"], 1)


# ---------------------------------------------------------------------------
# TestSymlinkMapping
# ---------------------------------------------------------------------------


class TestSymlinkMapping(unittest.TestCase):
    """Verify symlinks are mapped to their canonical manpage instead of extracted."""

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_mapped_after_extraction(
        self,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """A symlink whose canonical is extracted in the same batch gets mapped."""
        with _temp_db() as db_path:
            canonical = "/fake/distro/release/1/bio-eagle.1.gz"
            symlink = "/fake/distro/release/1/eagle.1.gz"
            mock_collect.return_value = [canonical, symlink]

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
                batch = BatchResult()
                for gz_path in files:
                    if on_start:
                        on_start(gz_path)
                    mp = _make_manpage(
                        "bio-eagle",
                        distro="distro",
                        release="release",
                        aliases=[("bio-eagle", 10)],
                    )
                    raw = _make_raw(sha256=gz_path)
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
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
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
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
            (_, call_files), _call_kwargs = mock_run.call_args
            self.assertEqual(call_files, [canonical])
            # Mapping inserted for symlink.
            result_store = Store(db_path, read_only=True)
            self.assertEqual(
                result_store.mapping_score("eagle", "distro/release/1/bio-eagle.1.gz"),
                10,
            )

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_skipped_when_canonical_missing(
        self,
        mock_collect,
        mock_make_ext,
        mock_run,
    ):
        """A symlink whose canonical is not in the DB gets a warning, not a mapping."""
        with _temp_db() as db_path:
            symlink = "/fake/distro/release/1/eagle.1.gz"
            mock_collect.return_value = [symlink]

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            with (
                patch("os.path.islink", return_value=True),
                patch(
                    "os.path.realpath",
                    return_value="/fake/distro/release/1/bio-eagle.1.gz",
                ),
            ):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
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
            result_store = Store(db_path, read_only=True)
            self.assertIsNone(
                result_store.mapping_score("eagle", "distro/release/1/bio-eagle.1.gz")
            )

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_already_mapped_at_score_10(
        self,
        mock_collect,
        mock_make_ext,
        mock_run,
    ):
        """Re-run: symlink mapping already exists at score 10, no change needed."""
        with _temp_db() as db_path:
            canonical = "/fake/distro/release/1/bio-eagle.1.gz"
            symlink = "/fake/distro/release/1/eagle.1.gz"
            mock_collect.return_value = [symlink]

            # Pre-populate: canonical manpage exists and mapping already at score 10.
            pre_store = Store.create(db_path)
            pre_store.add_manpage(
                _make_manpage("bio-eagle", distro="distro", release="release"),
                _make_raw(),
            )
            pre_store.add_mapping("eagle", "distro/release/1/bio-eagle.1.gz", score=10)
            pre_store.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            with (
                patch("os.path.islink", side_effect=lambda p: p == symlink),
                patch(
                    "os.path.realpath",
                    side_effect=lambda p: canonical if p == symlink else p,
                ),
            ):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
                        "extract",
                        "--mode",
                        "llm:openai/test-model",
                        "--batch",
                        "2",
                        "/fake/file.gz",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            # Score should still be 10 — no change.
            result_store = Store(db_path, read_only=True)
            self.assertEqual(
                result_store.mapping_score("eagle", "distro/release/1/bio-eagle.1.gz"),
                10,
            )

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_upgrades_lexgrog_alias_score(
        self,
        mock_collect,
        mock_make_ext,
        mock_run,
    ):
        """A lexgrog alias at score 1 is upgraded to score 10 by symlink mapping."""
        with _temp_db() as db_path:
            canonical = "/fake/distro/release/1/bio-eagle.1.gz"
            symlink = "/fake/distro/release/1/eagle.1.gz"
            mock_collect.return_value = [symlink]

            # Pre-populate: canonical manpage exists and mapping at score 1 (lexgrog alias).
            pre_store = Store.create(db_path)
            pre_store.add_manpage(
                _make_manpage("bio-eagle", distro="distro", release="release"),
                _make_raw(),
            )
            pre_store.add_mapping("eagle", "distro/release/1/bio-eagle.1.gz", score=1)
            pre_store.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            with (
                patch("os.path.islink", side_effect=lambda p: p == symlink),
                patch(
                    "os.path.realpath",
                    side_effect=lambda p: canonical if p == symlink else p,
                ),
            ):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
                        "extract",
                        "--mode",
                        "llm:openai/test-model",
                        "--batch",
                        "2",
                        "/fake/file.gz",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            # Score should be upgraded to 10.
            result_store = Store(db_path, read_only=True)
            self.assertEqual(
                result_store.mapping_score("eagle", "distro/release/1/bio-eagle.1.gz"),
                10,
            )

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    def test_symlink_cleans_up_stale_manpage(
        self,
        mock_collect,
        mock_make_ext,
        mock_run,
    ):
        """A file that was previously extracted as regular but is now a symlink
        should have its stale parsed_manpages row (and CASCADE mappings) removed."""
        with _temp_db() as db_path:
            canonical = "/fake/distro/release/1/context.1.gz"
            symlink = "/fake/distro/release/1/contextjit.1.gz"
            mock_collect.return_value = [symlink]

            # Pre-populate: both canonical and contextjit were previously
            # extracted as regular files.  Now contextjit is a symlink.
            pre_store = Store.create(db_path)
            pre_store.add_manpage(
                _make_manpage("context", distro="distro", release="release"),
                _make_raw(),
            )
            pre_store.add_manpage(
                _make_manpage("contextjit", distro="distro", release="release"),
                _make_raw(),
            )
            # Verify stale data exists.
            self.assertTrue(
                pre_store.has_manpage_source("distro/release/1/contextjit.1.gz")
            )
            self.assertEqual(
                pre_store.mapping_score(
                    "contextjit", "distro/release/1/contextjit.1.gz"
                ),
                10,
            )
            pre_store.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            with (
                patch("os.path.islink", side_effect=lambda p: p == symlink),
                patch(
                    "os.path.realpath",
                    side_effect=lambda p: canonical if p == symlink else p,
                ),
            ):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    [
                        "--db",
                        db_path,
                        "extract",
                        "--mode",
                        "llm:openai/test-model",
                        "--batch",
                        "2",
                        "/fake/file.gz",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            result_store = Store(db_path, read_only=True)
            # Stale parsed_manpages row should be gone.
            self.assertFalse(
                result_store.has_manpage_source("distro/release/1/contextjit.1.gz")
            )
            # Stale mapping should be gone (CASCADE).
            self.assertIsNone(
                result_store.mapping_score(
                    "contextjit", "distro/release/1/contextjit.1.gz"
                )
            )
            # Symlink mapping to canonical should exist.
            self.assertEqual(
                result_store.mapping_score(
                    "contextjit", "distro/release/1/context.1.gz"
                ),
                10,
            )


# ---------------------------------------------------------------------------
# TestContentDedup
# ---------------------------------------------------------------------------


class TestContentDedup(unittest.TestCase):
    """Verify content-identical files are deduplicated before LLM extraction."""

    @patch("explainshell.extraction.common.gz_sha256")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_identical_files_deduped_in_same_run(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        mock_sha,
    ):
        """Content-identical files should extract once and map the rest."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/x86_64-linux-gnu-gfortran-16.1.gz",
                "/fake/distro/release/1/aarch64-linux-gnu-gfortran-16.1.gz",
                "/fake/distro/release/1/other-tool.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])
            # First two files have the same hash, third is different.
            mock_sha.side_effect = lambda p: "aaa" if "gfortran" in p else "bbb"

            mock_make_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                batch = BatchResult()
                for gz_path in files:
                    if kwargs.get("on_start"):
                        kwargs["on_start"](gz_path)
                    source = mock_source(gz_path)
                    mp = _make_manpage_from_source(source)
                    raw = _make_raw(sha256="aaa" if "gfortran" in gz_path else "bbb")
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
                    if kwargs.get("on_result"):
                        kwargs["on_result"](gz_path, entry)
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            # Only 2 files should be passed to run() (x86_64 + other-tool),
            # aarch64 is deduped.
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(len(call_files), 2)
            self.assertIn(gz_files[0], call_files)
            self.assertNotIn(gz_files[1], call_files)
            self.assertIn(gz_files[2], call_files)
            # aarch64 gets a mapping to the x86_64 canonical source.
            result_store = Store(db_path, read_only=True)
            self.assertEqual(
                result_store.mapping_score(
                    "aarch64-linux-gnu-gfortran-16",
                    "distro/release/1/x86_64-linux-gnu-gfortran-16.1.gz",
                ),
                10,
            )

    @patch("explainshell.extraction.common.gz_sha256", return_value="existing-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_file_matching_db_hash_gets_mapped(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """A new file whose hash matches an already-extracted page gets mapped, not extracted."""
        with _temp_db() as db_path:
            gz_files = ["/fake/distro/release/1/aarch64-linux-gnu-gcc-16.1.gz"]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            # Pre-populate: canonical manpage already in DB with matching hash.
            pre_store = Store.create(db_path)
            pre_store.add_manpage(
                _make_manpage(
                    "x86_64-linux-gnu-gcc-16", distro="distro", release="release"
                ),
                _make_raw(sha256="existing-hash"),
            )
            pre_store.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            # run() should be called with an empty file list.
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(call_files, [])
            # Mapping created to the existing DB entry.
            result_store = Store(db_path, read_only=True)
            self.assertEqual(
                result_store.mapping_score(
                    "aarch64-linux-gnu-gcc-16",
                    "distro/release/1/x86_64-linux-gnu-gcc-16.1.gz",
                ),
                10,
            )

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_overwrite_bypasses_db_hash_dedup(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """--overwrite must re-extract even when the hash is already in the DB."""
        with _temp_db() as db_path:
            gz_files = ["/fake/distro/release/1/x86_64-linux-gnu-gcc-16.1.gz"]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            # Pre-populate: file already in DB with matching hash.
            pre_store = Store.create(db_path)
            pre_store.add_manpage(
                _make_manpage(
                    "x86_64-linux-gnu-gcc-16", distro="distro", release="release"
                ),
                _make_raw(sha256="same-hash"),
            )
            pre_store.close()

            mock_make_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                batch = BatchResult()
                for gz_path in files:
                    if kwargs.get("on_start"):
                        kwargs["on_start"](gz_path)
                    source = mock_source(gz_path)
                    mp = _make_manpage_from_source(source)
                    raw = _make_raw(sha256="same-hash")
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
                    if kwargs.get("on_result"):
                        kwargs["on_result"](gz_path, entry)
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "--overwrite",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            # The file must be extracted, not deduped.
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(len(call_files), 1)

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_cross_release_identical_files_not_deduped(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Identical files from different releases must both be extracted."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/24.04/1/foo.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            mock_make_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                batch = BatchResult()
                for gz_path in files:
                    if kwargs.get("on_start"):
                        kwargs["on_start"](gz_path)
                    source = mock_source(gz_path)
                    mp = _make_manpage_from_source(source)
                    raw = _make_raw(sha256="same-hash")
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
                    if kwargs.get("on_result"):
                        kwargs["on_result"](gz_path, entry)
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            # Both files should be passed to run() despite identical hashes.
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(len(call_files), 2)

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_cross_section_identical_files_not_deduped(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Identical files in different sections must both be extracted."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/8/foo.8.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            mock_make_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                batch = BatchResult()
                for gz_path in files:
                    if kwargs.get("on_start"):
                        kwargs["on_start"](gz_path)
                    source = mock_source(gz_path)
                    mp = _make_manpage_from_source(source)
                    raw = _make_raw(sha256="same-hash")
                    entry = ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SUCCESS,
                        mp=mp,
                        raw=raw,
                        stats=ExtractionStats(),
                    )
                    batch.n_succeeded += 1
                    if kwargs.get("on_result"):
                        kwargs["on_result"](gz_path, entry)
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            # Both files should be passed to run() despite identical hashes.
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(len(call_files), 2)


# ---------------------------------------------------------------------------
# TestFilterFlag
# ---------------------------------------------------------------------------


def _make_manpage_with_extractor(
    name: str,
    extractor: str,
    extraction_meta: dict | None = None,
    distro: str = "ubuntu",
    release: str = "26.04",
    section: str = "1",
) -> ParsedManpage:
    mp = _make_manpage(name, section=section, distro=distro, release=release)
    mp.extractor = extractor
    mp.extraction_meta = (
        ExtractionMeta.model_validate(extraction_meta) if extraction_meta else None
    )
    return mp


class TestFilterFlag(unittest.TestCase):
    """CLI tests for --filter-db."""

    def test_filter_db_requires_overwrite(self):
        runner = CliRunner()
        with _temp_db() as db_path:
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--filter-db",
                    "llm:openai/old",
                    "/fake/file.gz",
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("requires --overwrite", result.output)

    def test_filter_db_rejects_bogus_spec(self):
        runner = CliRunner()
        with _temp_db() as db_path:
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "bogus",
                    "/fake/file.gz",
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--filter-db", result.output)

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_db_llm_model_scope(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Only rows matching the filter model reach run()."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/1/bar.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor(
                    "foo", "llm", {"model": "openai/gpt-5-mini"}
                ),
                _make_raw(),
            )
            pre.add_manpage(
                _make_manpage_with_extractor("bar", "llm", {"model": "openai/gpt-5"}),
                _make_raw(),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/gpt-5-mini",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual([p.split("/")[-1] for p in call_files], ["foo.1.gz"])

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_db_repeatable_matches_any(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Repeated --filter-db lets rows matching any of the specs through."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/1/bar.1.gz",
                "/fake/ubuntu/26.04/1/baz.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor(
                    "foo", "llm", {"model": "openai/gpt-5-mini"}
                ),
                _make_raw(),
            )
            pre.add_manpage(
                _make_manpage_with_extractor(
                    "bar", "llm", {"model": "azure/gpt-5-mini/medium"}
                ),
                _make_raw(),
            )
            pre.add_manpage(
                _make_manpage_with_extractor("baz", "llm", {"model": "openai/gpt-5"}),
                _make_raw(),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/gpt-5-mini",
                    "--filter-db",
                    "llm:azure/gpt-5-mini/medium",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(
                sorted(p.split("/")[-1] for p in call_files),
                ["bar.1.gz", "foo.1.gz"],
            )

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_db_lets_new_files_through(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """Files not in the DB are extracted regardless of filter."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/1/new.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor("foo", "llm", {"model": "openai/old"}),
                _make_raw(),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/old",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(
                sorted(p.split("/")[-1] for p in call_files),
                ["foo.1.gz", "new.1.gz"],
            )

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_db_matches_extra_meta_keys(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """LLM filter matches rows that carry meta keys outside the schema."""
        with _temp_db() as db_path:
            gz_files = ["/fake/ubuntu/26.04/1/fb.1.gz"]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor(
                    "fb",
                    "llm",
                    {
                        "model": "openai/gpt-5-mini",
                        "legacy_field": True,
                        "reason": "low-confidence",
                    },
                ),
                _make_raw(),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/gpt-5-mini",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(len(call_files), 1)

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_skipped_row_doesnt_block_matching_sibling(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """An earlier non-matching DB row must not suppress a later matching
        DB row with the same content hash (finding 1 regression test)."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/1/bar.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor("foo", "llm", {"model": "openai/old"}),
                _make_raw(sha256="same-hash"),
            )
            pre.add_manpage(
                _make_manpage_with_extractor("bar", "llm", {"model": "openai/new"}),
                _make_raw(sha256="same-hash"),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/newer",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/new",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual([p.split("/")[-1] for p in call_files], ["bar.1.gz"])

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_new_file_first_doesnt_block_matching_db_row(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """A new file appearing earlier in the input must not suppress a
        filter-matching DB row with the same content hash (finding 1:
        input-order dependence in the content-dedup path)."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/newfile.1.gz",
                "/fake/ubuntu/26.04/1/bar.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor("bar", "llm", {"model": "openai/new"}),
                _make_raw(sha256="same-hash"),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/newer",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/new",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(
                sorted(p.split("/")[-1] for p in call_files),
                ["bar.1.gz", "newfile.1.gz"],
            )

    @patch("explainshell.extraction.common.gz_sha256", return_value="same-hash")
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_filter_skipped_row_doesnt_block_new_sibling(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ):
        """An earlier filter-skipped DB row must not suppress a later new
        (not-in-DB) file with the same content hash. The new file is
        extracted; we don't alias it onto the stale canonical."""
        with _temp_db() as db_path:
            gz_files = [
                "/fake/ubuntu/26.04/1/foo.1.gz",
                "/fake/ubuntu/26.04/1/clone.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])

            pre = Store.create(db_path)
            pre.add_manpage(
                _make_manpage_with_extractor("foo", "llm", {"model": "openai/old"}),
                _make_raw(sha256="same-hash"),
            )
            pre.close()

            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/new",
                    "--overwrite",
                    "--filter-db",
                    "llm:openai/new",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual([p.split("/")[-1] for p in call_files], ["clone.1.gz"])


# ---------------------------------------------------------------------------
# TestDiffDbCli
# ---------------------------------------------------------------------------


class TestDiffDbCli(unittest.TestCase):
    """CliRunner tests for the ``diff db`` command surface."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_db_success(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run
    ):
        """Basic diff db invocation succeeds."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_store = MagicMock()
        mock_store_create.return_value = mock_store
        mock_store.counts.return_value = {"manpages": 0, "mappings": 0}
        mock_make_ext.return_value = MagicMock()

        mock_run.return_value = BatchResult()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "diff",
                "db",
                "--mode",
                "llm:test-model",
                "/fake/a.1.gz",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        mock_store_create.assert_called_once_with("/tmp/test.db")
        mock_make_ext.assert_called_once()

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.store.Store.create")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_db_debug_threads_through(
        self, mock_source, mock_collect, mock_store_create, mock_make_ext, mock_run
    ):
        """--debug is forwarded to _run_diff_db."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_store_create.return_value = MagicMock()
        mock_make_ext.return_value = MagicMock()

        mock_run.return_value = BatchResult()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "diff",
                "db",
                "--mode",
                "llm:test-model",
                "--debug",
                "/fake/a.1.gz",
            ],
        )

        self.assertEqual(result.exit_code, 0)

        call_args = mock_make_ext.call_args
        cfg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("cfg")
        if isinstance(cfg, ExtractorConfig):
            self.assertIsNotNone(cfg.run_dir)
            self.assertTrue(cfg.debug)

    def test_diff_db_invalid_mode(self):
        """Invalid mode is rejected."""

        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "db", "--mode", "bogus", "/fake/a.1.gz"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid mode", result.output)


# ---------------------------------------------------------------------------
# TestDiffExtractorsCli
# ---------------------------------------------------------------------------


class TestDiffExtractorsCli(unittest.TestCase):
    """CliRunner tests for the ``diff extractors`` command surface."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path", return_value="fake/a.1.gz")
    def test_diff_extractors_success(
        self, mock_source, mock_collect, mock_make_ext, mock_run
    ):
        """Basic diff extractors invocation succeeds."""
        mock_collect.return_value = ["/fake/a.1.gz"]
        mock_make_ext.return_value = MagicMock()

        mock_run.return_value = BatchResult()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["diff", "extractors", "llm:m1..llm:m2", "/fake/a.1.gz"],
        )

        self.assertEqual(result.exit_code, 0)
        # Two extractors should be created (left and right).
        self.assertEqual(mock_make_ext.call_count, 2)

    def test_diff_extractors_invalid_spec_no_dots(self):
        """Spec without '..' is rejected."""

        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff", "extractors", "llm:m1-llm:m2", "/fake/a.1.gz"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid spec", result.output)

    def test_diff_extractors_invalid_mode_in_spec(self):
        """Invalid mode inside A..B spec is rejected."""

        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff", "extractors", "llm:m1..bogus", "/fake/a.1.gz"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("invalid mode", result.output)


# ---------------------------------------------------------------------------
# TestDiffExtractorsFailureHandling
# ---------------------------------------------------------------------------


class TestDiffExtractorsFailureHandling(unittest.TestCase):
    """Tests for _run_diff_extractors under partial/total failure."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_partial_failure_preserves_successful_stats(
        self, mock_source, mock_make_ext, mock_run
    ):
        """When one side fails, the successful side's stats are still counted."""
        mock_source.side_effect = lambda p: p.split("/")[-1]

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

        def _fake_run(ext, gz_files, **kwargs):
            files = left_files if mock_run.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run.side_effect = _fake_run

        result = _run_diff_extractors(
            ["/fake/a.1.gz", "/fake/b.1.gz"],
            ("llm", "m1"),
            ("llm", "m2"),
            None,
        )

        # File a: both OK → 100+200 input tokens
        # File b: left FAILED, right OK → right's 150 tokens preserved
        self.assertEqual(result.stats.input_tokens, 100 + 200 + 150)
        self.assertEqual(result.stats.output_tokens, 50 + 80 + 60)
        self.assertEqual(result.n_succeeded, 1)
        self.assertEqual(result.n_failed, 1)

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_failed_takes_precedence_over_skipped(
        self, mock_source, mock_make_ext, mock_run
    ):
        """When one side is SKIPPED and the other FAILED, outcome is FAILED."""
        mock_source.side_effect = lambda p: p.split("/")[-1]

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

        def _fake_run(ext, gz_files, **kwargs):
            files = left_files if mock_run.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run.side_effect = _fake_run

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("llm", "m1"),
            ("llm", "m2"),
            None,
        )

        self.assertEqual(result.n_failed, 1)
        self.assertEqual(result.n_skipped, 0)

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_both_skipped_yields_skipped_outcome(
        self, mock_source, mock_make_ext, mock_run
    ):
        """When both extractors skip, outcome is SKIPPED (not FAILED)."""
        mock_source.side_effect = lambda p: p.split("/")[-1]

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

        def _fake_run(ext, gz_files, **kwargs):
            files = left_files if mock_run.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run.side_effect = _fake_run

        result = _run_diff_extractors(
            ["/fake/a.1.gz"],
            ("llm", "m1"),
            ("llm", "m2"),
            None,
        )

        self.assertEqual(result.n_skipped, 1)
        self.assertEqual(result.n_failed, 0)


class TestDiffExtractorLabels(unittest.TestCase):
    """Labels in diff output must include model when present."""

    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.manager.config.source_from_path")
    def test_llm_vs_llm_labels_include_model(
        self, mock_source, mock_make_ext, mock_run
    ):
        """When both sides are llm:<model>, labels must distinguish them."""
        mock_source.side_effect = lambda p: p.split("/")[-1]

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

        def _fake_run(ext, gz_files, **kwargs):
            files = left_files if mock_run.call_count == 1 else right_files
            on_result = kwargs.get("on_result")
            if on_result:
                for f in files:
                    on_result(f.gz_path, f)
            return BatchResult()

        mock_run.side_effect = _fake_run

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
        self, gz_path: str, short_path: str, store: Store
    ) -> list[str]:
        """Run _run_diff_db and return captured log lines."""

        fake_mp = _make_manpage_from_source(short_path)
        fake_raw = _make_raw()

        entry = ExtractionResult(
            gz_path=gz_path,
            outcome=ExtractionOutcome.SUCCESS,
            mp=fake_mp,
            raw=fake_raw,
            stats=ExtractionStats(),
        )

        with (
            patch("explainshell.manager.make_extractor") as mock_ext,
            patch(
                "explainshell.manager.config.source_from_path",
                return_value=short_path,
            ),
            patch("explainshell.manager.run") as mock_run,
        ):
            mock_ext.return_value = MagicMock()

            def _fake_run(ext, files, **kwargs):
                on_result = kwargs.get("on_result")
                if on_result:
                    on_result(gz_path, entry)
                return BatchResult()

            mock_run.side_effect = _fake_run

            with self.assertLogs("explainshell.manager", level=logging.INFO) as cm:
                _run_diff_db([gz_path], "llm", "test-model", None, False, store)

        return cm.output

    def test_exact_source_match_preferred(self):
        """When the exact source path exists in DB, use it directly."""
        with _temp_db() as db_path:
            real_store = Store.create(db_path)
            # Insert find in two releases so both exact-source and name lookups
            # could succeed.  Give the 26.04 entry a distinctive synopsis so
            # we can tell which one the diff resolved.
            mp_25 = _make_manpage("find", distro="ubuntu", release="26.04")
            mp_25.synopsis = "old synopsis"
            real_store.add_manpage(mp_25, _make_raw())
            real_store.add_manpage(
                _make_manpage("find", distro="ubuntu", release="26.04"),
                _make_raw(),
            )

            logs = self._run_diff_db_with_store(
                "/manpages/ubuntu/26.04/1/find.1.gz",
                "ubuntu/26.04/1/find.1.gz",
                real_store,
            )

            log_text = "\n".join(logs)
            self.assertNotIn("not in DB", log_text)
            # Must NOT show the 26.04 synopsis — exact source (26.04) should
            # have been preferred over the name-based fallback.
            self.assertNotIn("old synopsis", log_text)
            real_store.close()

    def test_falls_back_to_name_when_source_not_found(self):
        """When exact source is not in DB, fall back to name lookup."""
        with _temp_db() as db_path:
            real_store = Store.create(db_path)
            # Insert find under 26.04 only.  The exact source lookup for
            # ubuntu/26.04 will fail, so _run_diff_db must fall back to the
            # name-based lookup ("find") which resolves to this entry.
            mp = _make_manpage("find", distro="ubuntu", release="26.04")
            mp.synopsis = "fallback synopsis"
            real_store.add_manpage(mp, _make_raw())

            logs = self._run_diff_db_with_store(
                "/manpages/ubuntu/26.04/1/find.1.gz",
                "ubuntu/26.04/1/find.1.gz",
                real_store,
            )

            log_text = "\n".join(logs)
            self.assertNotIn("not in DB", log_text)
            # The diff must show the fallback entry's synopsis, proving the
            # name-based lookup was used after the exact source miss.
            self.assertIn("fallback synopsis", log_text)
            real_store.close()

    def test_both_lookups_fail_logs_not_in_db(self):
        """When neither source nor name is in DB, log 'not in DB'."""
        with _temp_db() as db_path:
            real_store = Store.create(db_path)
            # Store is empty — both lookups will fail.

            logs = self._run_diff_db_with_store(
                "/manpages/ubuntu/26.04/1/find.1.gz",
                "ubuntu/26.04/1/find.1.gz",
                real_store,
            )

            log_text = "\n".join(logs)
            self.assertIn("not in DB", log_text)
            real_store.close()


# ---------------------------------------------------------------------------
# TestDbPathValidation
# ---------------------------------------------------------------------------


class TestDbPathValidation(unittest.TestCase):
    """CLI gives clean errors for missing/nonexistent --db."""

    def test_no_db_set(self):
        """Commands that need a DB fail cleanly when --db is not set."""

        runner = CliRunner()
        result = runner.invoke(cli, ["show", "stats"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No database path", result.output)

    def test_nonexistent_db(self):
        """Read-only commands fail cleanly when DB file doesn't exist."""

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", "/tmp/does-not-exist-12345.db", "show", "stats"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Database not found", result.output)


# ---------------------------------------------------------------------------
# TestShowCli — uses real temp DB
# ---------------------------------------------------------------------------


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
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "stats"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("parsed_manpages:   2", result.output)
        self.assertIn("ubuntu/26.04", result.output)

    def test_show_distros(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "distros"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("ubuntu/26.04", result.output)

    def test_show_manpage(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "manpage", "tar"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("name: tar", result.output)
        self.assertIn("options: 2", result.output)
        self.assertIn("--create", result.output)

    def test_show_manpage_distro_release(self):
        # Add the same command under a second distro.
        self.store.add_manpage(
            _make_manpage("tar", distro="arch", release="latest"),
            _make_raw(),
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                self.db_path,
                "show",
                "manpage",
                "tar",
                "--distro",
                "arch",
                "--release",
                "latest",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("source: arch/latest/1/tar.1.gz", result.output)

    def test_show_manpage_distro_without_release(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--db", self.db_path, "show", "manpage", "tar", "--distro", "ubuntu"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--distro and --release must be used together", result.output)

    def test_show_manpage_release_without_distro(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--db", self.db_path, "show", "manpage", "tar", "--release", "26.04"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--distro and --release must be used together", result.output)

    def test_show_manpage_not_found(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "manpage", "nonexistent"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Not found", result.output)

    def test_show_sections(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "sections", "ubuntu", "26.04"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("1", result.output)

    def test_show_manpages(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--db", self.db_path, "show", "manpages", "ubuntu/26.04/1/"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("tar.1.gz", result.output)
        self.assertIn("echo.1.gz", result.output)

    def test_show_mappings(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "mappings"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("tar ->", result.output)
        self.assertIn("echo ->", result.output)

    def test_show_events_empty(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "events"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No events recorded", result.output)

    def test_show_events_date_format(self):
        self.store.log_event(
            "extraction",
            {
                "version": 1,
                "command": "extract",
                "timestamp": "2026-04-14T10:30:00+00:00",
                "git": {"commit": None, "commit_short": None, "dirty": None},
                "config": {"mode": "llm"},
                "elapsed_seconds": 1.0,
                "summary": {"succeeded": 0, "skipped": 0, "failed": 0},
                "db_before": {"manpages": 0, "mappings": 0},
                "db_after": {"manpages": 0, "mappings": 0},
            },
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "events"])

        self.assertEqual(result.exit_code, 0)
        # Short date format: "YYYY-MM-DD HH:MM"
        self.assertRegex(result.output, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        # Humanized delta in parentheses (e.g. "now", "2 days ago")
        self.assertRegex(result.output, r"\(.*ago\)|now\)")

    def test_show_events_extraction(self):
        self.store.log_event(
            "extraction",
            {
                "version": 1,
                "command": "extract",
                "timestamp": "2026-04-14T10:00:00+00:00",
                "git": {"commit": "abc123", "commit_short": "abc", "dirty": False},
                "config": {"mode": "llm", "model": "openai/gpt-5"},
                "elapsed_seconds": 5.0,
                "summary": {
                    "succeeded": 10,
                    "skipped": 5,
                    "failed": 1,
                },
                "db_before": {"manpages": 100, "mappings": 200},
                "db_after": {"manpages": 110, "mappings": 220},
            },
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "events"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("event:    extraction", result.output)
        self.assertIn("mode:     llm", result.output)
        self.assertIn("model:    openai/gpt-5", result.output)
        self.assertIn("result:   ok=10 skip=5 fail=1", result.output)
        self.assertIn("db:       110(+10) mappings=220(+20)", result.output)
        self.assertIn("reason:   (no reason provided)", result.output)

    def test_show_events_extraction_with_reason(self):
        self.store.log_event(
            "extraction",
            {
                "version": 1,
                "command": "extract",
                "timestamp": "2026-05-04T10:00:00+00:00",
                "git": {"commit": "abc123", "commit_short": "abc", "dirty": False},
                "config": {"mode": "llm", "model": "openai/gpt-5"},
                "elapsed_seconds": 5.0,
                "summary": {"succeeded": 10, "skipped": 0, "failed": 0},
                "db_before": {"manpages": 100, "mappings": 200},
                "db_after": {"manpages": 110, "mappings": 220},
                "reason": "fix garbled emphasis after mandoc 89d8f45",
            },
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "events"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "reason:   fix garbled emphasis after mandoc 89d8f45", result.output
        )

    def test_show_events_limit(self):
        for i in range(5):
            self.store.log_event(
                "extraction",
                {
                    "version": 1,
                    "command": "extract",
                    "timestamp": f"2026-04-{10 + i}T10:00:00+00:00",
                    "git": {"commit": None, "commit_short": None, "dirty": None},
                    "config": {"mode": "llm"},
                    "elapsed_seconds": 1.0,
                    "summary": {"succeeded": i, "skipped": 0, "failed": 0},
                    "db_before": {"manpages": 0, "mappings": 0},
                    "db_after": {"manpages": 0, "mappings": 0},
                },
            )

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "show", "events", "-n", "2"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.count("event:"), 2)


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

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "db-check"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No issues found", result.output)

    def test_reports_issues(self):
        """Insert an orphaned mapping so db-check has something to report."""
        self.store._conn.execute("PRAGMA foreign_keys = OFF")
        self.store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("ghost", "ubuntu/26.04/1/ghost.1.gz", 10),
        )
        self.store._conn.commit()
        self.store._conn.execute("PRAGMA foreign_keys = ON")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", self.db_path, "db-check"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("orphaned mapping", result.output)

    def test_nonexistent_db(self):
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

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("os.path.islink", return_value=False)
    @patch(
        "explainshell.manager.config.source_from_path",
        side_effect=lambda p: f"fake/release/1/{os.path.basename(p)}",
    )
    def test_extract_expands_at_file(
        self,
        _mock_source: MagicMock,
        _mock_link: MagicMock,
        _mock_sha: MagicMock,
    ) -> None:
        """@file arg is expanded to the file's contents and reaches the classifier."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n")
            f.flush()
            list_path = f.name

        try:
            with _temp_db() as db_path:
                Store.create(db_path).close()
                runner = CliRunner()
                with self.assertLogs("explainshell.manager", level="INFO") as cm:
                    result = runner.invoke(
                        cli,
                        [
                            "--db",
                            db_path,
                            "extract",
                            "--mode",
                            "llm:test-model",
                            "--dry-run",
                            f"@{list_path}",
                        ],
                    )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("fake/release/1/echo.1.gz", "\n".join(cm.output))
        finally:
            os.unlink(list_path)

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("os.path.islink", return_value=False)
    @patch(
        "explainshell.manager.config.source_from_path",
        side_effect=lambda p: f"fake/release/1/{os.path.basename(p)}",
    )
    def test_extract_at_file_skips_blanks_and_comments(
        self,
        _mock_source: MagicMock,
        _mock_link: MagicMock,
        _mock_sha: MagicMock,
    ) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n\n# comment\n  \n")
            f.flush()
            list_path = f.name

        try:
            with _temp_db() as db_path:
                Store.create(db_path).close()
                runner = CliRunner()
                with self.assertLogs("explainshell.manager", level="INFO") as cm:
                    result = runner.invoke(
                        cli,
                        [
                            "--db",
                            db_path,
                            "extract",
                            "--mode",
                            "llm:test-model",
                            "--dry-run",
                            f"@{list_path}",
                        ],
                    )

            self.assertEqual(result.exit_code, 0, result.output)
            msgs = "\n".join(cm.output)
            self.assertIn("fake/release/1/echo.1.gz", msgs)
            self.assertIn("(1 total)", msgs)
        finally:
            os.unlink(list_path)

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("os.path.islink", return_value=False)
    @patch(
        "explainshell.manager.config.source_from_path",
        side_effect=lambda p: f"fake/release/1/{os.path.basename(p)}",
    )
    def test_extract_mixed_plain_and_at_file(
        self,
        _mock_source: MagicMock,
        _mock_link: MagicMock,
        _mock_sha: MagicMock,
    ) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("/fake/echo.1.gz\n")
            f.flush()
            list_path = f.name

        try:
            with _temp_db() as db_path:
                Store.create(db_path).close()
                runner = CliRunner()
                with self.assertLogs("explainshell.manager", level="INFO") as cm:
                    result = runner.invoke(
                        cli,
                        [
                            "--db",
                            db_path,
                            "extract",
                            "--mode",
                            "llm:test-model",
                            "--dry-run",
                            "/fake/other.1.gz",
                            f"@{list_path}",
                        ],
                    )

            self.assertEqual(result.exit_code, 0, result.output)
            msgs = "\n".join(cm.output)
            self.assertIn("fake/release/1/other.1.gz", msgs)
            self.assertIn("fake/release/1/echo.1.gz", msgs)
        finally:
            os.unlink(list_path)


# ---------------------------------------------------------------------------
# TestExtractionReport
# ---------------------------------------------------------------------------


class TestExtractionReport(unittest.TestCase):
    """Verify _write_report produces correct report.json files."""

    def setUp(self) -> None:
        self._run_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self._run_dir, ignore_errors=True)

    def _read_report(self) -> dict:
        path = os.path.join(self._run_dir, "report.json")
        self.assertTrue(
            os.path.isfile(path), f"report.json not found in {self._run_dir}"
        )
        with open(path) as f:
            return json.load(f)

    def _make_report(self, **overrides):
        defaults = {
            "timestamp": "2026-03-30T12:00:00+00:00",
            "git": GitInfo(commit="abc123", commit_short="abc123", dirty=False),
            "config": ExtractConfig(mode="llm", model="openai/test-model"),
            "elapsed_seconds": 1.0,
            "summary": ExtractSummary(succeeded=1, skipped=0, failed=0),
            "db_before": DbCounts(manpages=10, mappings=50),
            "db_after": DbCounts(manpages=11, mappings=55),
        }
        defaults.update(overrides)
        return ExtractionReport(**defaults)

    def test_report_schema(self) -> None:
        """report.json has the expected top-level fields and values."""

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

        report = self._make_report(batch_manifest=None)
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertNotIn("batch_manifest", data)
        self.assertNotIn("reason", data)

    def test_reason_embedded(self) -> None:
        """reason string is included in the JSON when provided."""

        report = self._make_report(reason="bulk re-run after mandoc emphasis fix")
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertEqual(data["reason"], "bulk re-run after mandoc emphasis fix")

    def test_batch_manifest_embedded(self) -> None:
        """batch_manifest dict is included when provided."""

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

        standalone = os.path.join(self._run_dir, "batch-manifest.json")
        with open(standalone, "w") as f:
            f.write("{}")

        report = self._make_report(batch_manifest={"version": 1})
        _write_report(self._run_dir, report)

        self.assertFalse(os.path.isfile(standalone))

    def test_interrupted_report(self) -> None:
        """Interrupted runs record interrupted=true in the summary."""

        report = self._make_report(
            summary=ExtractSummary(succeeded=0, skipped=0, failed=0, interrupted=True),
        )
        _write_report(self._run_dir, report)

        data = self._read_report()
        self.assertTrue(data["summary"]["interrupted"])

    def test_fatal_error_report(self) -> None:
        """Fatal errors are recorded in summary.fatal_error."""

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


class TestOptionCountSummary(unittest.TestCase):
    """OptionCountSummary.from_counts: histogram math."""

    def test_empty_returns_zero_summary(self) -> None:
        """Empty counts yield a zero-everything summary, never None."""
        from explainshell.extraction.report import OptionCountSummary

        s = OptionCountSummary.from_counts([])
        self.assertEqual(s.n, 0)
        self.assertEqual(s.total, 0)
        self.assertEqual(s.mean, 0.0)
        self.assertEqual(s.max, 0)
        self.assertEqual(s.buckets, {"0": 0, "1-5": 0, "6-15": 0, "16-50": 0, "50+": 0})

    def test_buckets_and_stats(self) -> None:
        from explainshell.extraction.report import OptionCountSummary

        counts = [0, 0, 1, 3, 5, 7, 10, 15, 16, 30, 50, 51, 100]
        s = OptionCountSummary.from_counts(counts)
        assert s is not None
        self.assertEqual(s.n, 13)
        self.assertEqual(s.total, sum(counts))
        self.assertEqual(s.max, 100)
        self.assertEqual(
            s.buckets,
            {"0": 2, "1-5": 3, "6-15": 3, "16-50": 3, "50+": 2},
        )
        # mean = sum/n; median is the middle of sorted list (n=13 -> index 6 = 10)
        self.assertAlmostEqual(s.mean, sum(counts) / 13, places=2)
        self.assertEqual(s.median, 10.0)

    def test_single_count_p90_fallback(self) -> None:
        """statistics.quantiles needs >= 2 points; single point falls back."""
        from explainshell.extraction.report import OptionCountSummary

        s = OptionCountSummary.from_counts([7])
        assert s is not None
        self.assertEqual(s.n, 1)
        self.assertEqual(s.p90, 7.0)


class TestReportFailureAndSkipReporting(unittest.TestCase):
    """report.json captures classified failures, skips, option histogram, and tokens."""

    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_classified_outcomes_recorded(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
    ) -> None:
        from explainshell.errors import FailureReason

        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/ok.1.gz",
                "/fake/distro/release/1/bad.1.gz",
                "/fake/distro/release/1/skipped.1.gz",
            ]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])
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
                batch = BatchResult()
                # ok: SUCCESS with 4 options
                ok = files[0]
                mp = _make_manpage_from_source(mock_source(ok))
                mp.options = [
                    Option(text=f"-{i}", short=[f"-{i}"], long=[], has_argument=False)
                    for i in range(4)
                ]
                batch.n_succeeded += 1
                batch.stats.input_tokens = 1234
                batch.stats.output_tokens = 567
                batch.stats.reasoning_tokens = 89
                batch.stats.chunks = 2
                batch.stats.plain_text_len = 4096
                if on_result:
                    on_result(
                        ok,
                        ExtractionResult(
                            gz_path=ok,
                            outcome=ExtractionOutcome.SUCCESS,
                            mp=mp,
                            raw=_make_raw(sha256=ok),
                            stats=ExtractionStats(),
                        ),
                    )
                # bad: FAILED with line-span coverage classification
                bad = files[1]
                batch.n_failed += 1
                if on_result:
                    on_result(
                        bad,
                        ExtractionResult(
                            gz_path=bad,
                            outcome=ExtractionOutcome.FAILED,
                            error="line-span coverage 4.5x exceeds 3.0x limit",
                            reason_class=FailureReason.LINE_SPAN_COVERAGE,
                        ),
                    )
                # skipped: SKIPPED with manpage_too_large classification
                sk = files[2]
                batch.n_skipped += 1
                if on_result:
                    on_result(
                        sk,
                        ExtractionResult(
                            gz_path=sk,
                            outcome=ExtractionOutcome.SKIPPED,
                            error="manpage too large (600,000 chars, limit 500,000)",
                            reason_class=FailureReason.MANPAGE_TOO_LARGE,
                        ),
                    )
                return batch

            mock_run.side_effect = _fake_run

            runner = CliRunner()
            with tempfile.TemporaryDirectory() as run_dir:
                with patch(
                    "explainshell.manager._attach_run_log", return_value=run_dir
                ):
                    result = runner.invoke(
                        cli,
                        [
                            "--db",
                            db_path,
                            "extract",
                            "--mode",
                            "llm:openai/test-model",
                            "/fake/file.gz",
                        ],
                    )

                # Failed file → nonzero exit; report is still written.
                self.assertEqual(result.exit_code, 1, result.output)
                with open(os.path.join(run_dir, "report.json")) as f:
                    data = json.load(f)

                # failures
                self.assertEqual(len(data["failures"]), 1)
                self.assertEqual(
                    data["failures"][0]["reason_class"], "line_span_coverage"
                )
                self.assertEqual(
                    data["failures"][0]["path"], "distro/release/1/bad.1.gz"
                )
                self.assertIn("line-span coverage", data["failures"][0]["message"])

                # skips
                self.assertEqual(len(data["skips"]), 1)
                self.assertEqual(data["skips"][0]["reason_class"], "manpage_too_large")

                # token usage
                self.assertEqual(data["usage"]["input_tokens"], 1234)
                self.assertEqual(data["usage"]["output_tokens"], 567)
                self.assertEqual(data["usage"]["reasoning_tokens"], 89)
                self.assertEqual(data["usage"]["chunks"], 2)
                self.assertEqual(data["usage"]["plain_text_chars"], 4096)

                # option histogram
                self.assertEqual(data["option_counts"]["n"], 1)
                self.assertEqual(data["option_counts"]["total"], 4)
                self.assertEqual(data["option_counts"]["max"], 4)
                self.assertEqual(data["option_counts"]["buckets"]["1-5"], 1)


class TestThrowSitesTagged(unittest.TestCase):
    """Each FailureReason has a corresponding throw site that emits it.

    Drives the extractor pipeline with carefully crafted inputs and asserts
    the resulting exception carries the expected ``reason_class``.
    """

    def test_invalid_response_no_json(self) -> None:
        from explainshell.errors import ExtractionError, FailureReason
        from explainshell.extraction.llm.response import parse_json_response

        with self.assertRaises(ExtractionError) as cm:
            parse_json_response("I'm sorry, but I cannot assist.")
        self.assertEqual(cm.exception.reason_class, FailureReason.INVALID_RESPONSE)

    def test_invalid_json(self) -> None:
        from explainshell.errors import ExtractionError, FailureReason
        from explainshell.extraction.llm.response import parse_json_response

        with self.assertRaises(ExtractionError) as cm:
            # Malformed JSON, no escapes to repair.
            parse_json_response("{not even close}")
        self.assertEqual(cm.exception.reason_class, FailureReason.INVALID_JSON)

    def test_invalid_schema(self) -> None:
        from explainshell.errors import ExtractionError, FailureReason
        from explainshell.extraction.llm.response import process_llm_result

        with self.assertRaises(ExtractionError) as cm:
            # Valid JSON, missing 'options' key.
            process_llm_result('{"foo": []}')
        self.assertEqual(cm.exception.reason_class, FailureReason.INVALID_SCHEMA)

    def test_line_span_coverage(self) -> None:
        from explainshell.errors import ExtractionError, FailureReason
        from explainshell.extraction.postprocess import sanity_check_line_spans
        from explainshell.models import Option

        # 5 options each spanning 1..20 = 100 lines covered, max_end=20 -> 5x.
        opts = [
            Option(
                text="x",
                short=[f"-{c}"],
                long=[],
                has_argument=False,
                meta={"lines": [1, 20]},
            )
            for c in "abcde"
        ]
        with self.assertRaises(ExtractionError) as cm:
            sanity_check_line_spans(opts)
        self.assertEqual(cm.exception.reason_class, FailureReason.LINE_SPAN_COVERAGE)

    def test_mandoc_failed(self) -> None:
        from explainshell.errors import ExtractionError, FailureReason
        from explainshell.extraction.llm import text as text_mod

        # Patch subprocess.run to simulate mandoc failure.
        with patch.object(text_mod, "subprocess") as mock_subp:
            mock_subp.run.return_value = MagicMock(
                returncode=1, stdout="", stderr="mandoc: parse error"
            )
            with (
                patch("os.path.isfile", return_value=True),
                self.assertRaises(ExtractionError) as cm,
            ):
                text_mod.get_manpage_text("/fake/path.gz")
        self.assertEqual(cm.exception.reason_class, FailureReason.MANDOC_FAILED)

    def test_blacklisted_skip(self) -> None:
        """Blacklisted source raises SkippedExtraction(reason_class=BLACKLISTED)."""
        from explainshell.errors import FailureReason, SkippedExtraction
        from explainshell.extraction.llm import extractor as ext_mod

        # Pick any entry from the blacklist.
        blacklisted = next(iter(ext_mod._BLACKLISTED_SOURCES))
        e = ext_mod.LLMExtractor.__new__(ext_mod.LLMExtractor)

        with (
            patch("explainshell.config.source_from_path", return_value=blacklisted),
            self.assertRaises(SkippedExtraction) as cm,
        ):
            e.prepare("/fake/blacklisted.gz")
        self.assertEqual(cm.exception.reason_class, FailureReason.BLACKLISTED)

    def test_classify_provider_error(self) -> None:
        from explainshell.errors import FailureReason
        from explainshell.extraction.llm.extractor import LLMExtractor

        cf = LLMExtractor._classify_provider_error(
            Exception(
                "Error code: 400 - {'error': {'code': 'content_filter', "
                "'message': 'response was filtered'}}"
            )
        )
        self.assertEqual(cf, FailureReason.CONTENT_FILTER)

        other = LLMExtractor._classify_provider_error(
            Exception("Error code: 502 - bad gateway")
        )
        self.assertEqual(other, FailureReason.PROVIDER_ERROR)


class TestExtractSizeFilters(unittest.TestCase):
    """Verify --small-only / --large-only partition the input at the hardcoded threshold."""

    @patch("explainshell.manager.os.path.getsize")
    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_small_only_keeps_files_at_or_below_threshold(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
        mock_getsize,
    ) -> None:
        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/tiny.1.gz",
                "/fake/distro/release/1/edge.1.gz",
                "/fake/distro/release/1/big.1.gz",
            ]
            sizes = {gz_files[0]: 1024, gz_files[1]: 2048, gz_files[2]: 4096}
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])
            mock_getsize.side_effect = lambda p: sizes[p]
            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "--small-only",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual(
                sorted(p.split("/")[-1] for p in call_files),
                ["edge.1.gz", "tiny.1.gz"],
            )

    @patch("explainshell.manager.os.path.getsize")
    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_large_only_keeps_files_above_threshold(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
        mock_getsize,
    ) -> None:
        with _temp_db() as db_path:
            gz_files = [
                "/fake/distro/release/1/tiny.1.gz",
                "/fake/distro/release/1/edge.1.gz",
                "/fake/distro/release/1/big.1.gz",
            ]
            sizes = {gz_files[0]: 1024, gz_files[1]: 2048, gz_files[2]: 4096}
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])
            mock_getsize.side_effect = lambda p: sizes[p]
            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "--db",
                    db_path,
                    "extract",
                    "--mode",
                    "llm:openai/test-model",
                    "--large-only",
                    "/fake/file.gz",
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            (_, call_files), _ = mock_run.call_args
            self.assertEqual([p.split("/")[-1] for p in call_files], ["big.1.gz"])

    def test_small_and_large_mutually_exclusive(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db",
                "/tmp/test.db",
                "extract",
                "--mode",
                "llm:test-model",
                "--large-only",
                "--small-only",
                "/fake/file.gz",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(
            "--small-only and --large-only are mutually exclusive", result.output
        )

    @patch("explainshell.manager.os.path.getsize")
    @patch("explainshell.extraction.common.gz_sha256", side_effect=lambda p: p)
    @patch("explainshell.manager.run")
    @patch("explainshell.manager.make_extractor")
    @patch("explainshell.util.collect_gz_files")
    @patch("explainshell.manager.config.source_from_path")
    def test_size_filter_recorded_in_report(
        self,
        mock_source,
        mock_collect,
        mock_make_ext,
        mock_run,
        _mock_sha,
        mock_getsize,
    ) -> None:
        with _temp_db() as db_path:
            gz_files = ["/fake/distro/release/1/foo.1.gz"]
            mock_collect.return_value = gz_files
            mock_source.side_effect = lambda p: "/".join(p.split("/")[-4:])
            mock_getsize.return_value = 1000
            mock_make_ext.return_value = MagicMock()
            mock_run.return_value = BatchResult()

            runner = CliRunner()
            with tempfile.TemporaryDirectory() as run_dir:
                with patch(
                    "explainshell.manager._attach_run_log", return_value=run_dir
                ):
                    result = runner.invoke(
                        cli,
                        [
                            "--db",
                            db_path,
                            "extract",
                            "--mode",
                            "llm:openai/test-model",
                            "--small-only",
                            "/fake/file.gz",
                        ],
                    )

                self.assertEqual(result.exit_code, 0, result.output)
                with open(os.path.join(run_dir, "report.json")) as f:
                    data = json.load(f)
                self.assertTrue(data["config"]["small_only"])
                # large_only defaults to False; exclude_none keeps False values.
                self.assertFalse(data["config"].get("large_only", False))


class TestCollectGzFilesValidation(unittest.TestCase):
    """collect_gz_files must reject non-.gz file paths."""

    def test_rejects_non_gz_file(self) -> None:
        with self.assertRaises(ValueError) as cm:
            collect_gz_files(["somefile.txt"])
        self.assertIn("somefile.txt", str(cm.exception))
        self.assertIn("@somefile.txt", str(cm.exception))

    def test_accepts_gz_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".gz") as f:
            result = collect_gz_files([f.name])
            self.assertEqual(result, [os.path.abspath(f.name)])


if __name__ == "__main__":
    unittest.main()
