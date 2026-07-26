"""Unit tests for explainshell.extraction.prefilter."""

import unittest
from unittest.mock import patch

from explainshell.extraction.prefilter import (
    AlreadyStored,
    Classified,
    Classifier,
    ContentDup,
    FilterSkip,
    SizeSkip,
    Symlink,
    Work,
    apply_decisions,
)
from explainshell.models import ExtractionMeta


def _short(p: str) -> str:
    """Match `config.source_from_path` for paths shaped /any/.../distro/rel/sec/file.gz."""
    return "/".join(p.split("/")[-4:])


class _FakeStore:
    """Minimal Store stand-in covering the four methods Classifier/apply touch."""

    def __init__(
        self,
        *,
        known_sha256s: dict[str, str] | None = None,
        extractor_info: dict[str, tuple[str, ExtractionMeta]] | None = None,
        sources_in_db: set[str] | None = None,
    ) -> None:
        self._sha = known_sha256s or {}
        self._info = extractor_info or {}
        self._in_db = sources_in_db or set()
        self.deleted: list[str] = []

    def known_sha256s(self) -> dict[str, str]:
        return dict(self._sha)

    def extractor_info_index(self) -> dict[str, tuple[str, ExtractionMeta]]:
        return dict(self._info)

    def has_manpage_source(self, source: str) -> bool:
        return source in self._in_db

    def delete_manpage(self, source: str) -> None:
        self.deleted.append(source)
        self._in_db.discard(source)


def _make_classifier(
    s: _FakeStore,
    *,
    overwrite: bool = False,
    filter_specs: list[tuple[str, str | None]] | None = None,
    small_only: bool = False,
    large_only: bool = False,
    threshold: int = 2048,
    inputs: set[str] | None = None,
) -> Classifier:
    return Classifier(
        s=s,  # type: ignore[arg-type]
        overwrite=overwrite,
        filter_specs=filter_specs or [],
        small_only=small_only,
        large_only=large_only,
        size_threshold=threshold,
        normalized_inputs=inputs or set(),
    )


# Patch config.source_from_path globally for all tests via a module-level decorator.
_PATCH_SOURCE = patch(
    "explainshell.extraction.prefilter.config.source_from_path", side_effect=_short
)


class TestClassifySize(unittest.TestCase):
    def setUp(self) -> None:
        _PATCH_SOURCE.start()
        self.addCleanup(_PATCH_SOURCE.stop)

    @patch("os.path.islink", return_value=False)
    @patch("os.path.getsize", return_value=4096)
    def test_small_only_skips_large_file(self, _gs, _il) -> None:
        c = _make_classifier(_FakeStore(), small_only=True, threshold=2048)
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        self.assertIsInstance(d, SizeSkip)
        assert isinstance(d, SizeSkip)
        self.assertEqual(d.direction, "small")
        self.assertEqual(d.size, 4096)

    @patch("os.path.islink", return_value=False)
    @patch("os.path.getsize", return_value=2048)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_small_only_passes_at_threshold(self, _h, _gs, _il) -> None:
        c = _make_classifier(_FakeStore(), small_only=True, threshold=2048)
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    @patch("os.path.getsize", return_value=2048)
    def test_large_only_skips_at_threshold(self, _gs, _il) -> None:
        c = _make_classifier(_FakeStore(), large_only=True, threshold=2048)
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        self.assertIsInstance(d, SizeSkip)
        assert isinstance(d, SizeSkip)
        self.assertEqual(d.direction, "large")

    @patch("os.path.islink", return_value=False)
    @patch("os.path.getsize", return_value=4096)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_large_only_passes_above_threshold(self, _h, _gs, _il) -> None:
        c = _make_classifier(_FakeStore(), large_only=True, threshold=2048)
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)


class TestClassifySymlink(unittest.TestCase):
    def setUp(self) -> None:
        _PATCH_SOURCE.start()
        self.addCleanup(_PATCH_SOURCE.stop)

    @patch("os.path.realpath", side_effect=lambda p: "/x/u/26.04/1/canon.1.gz")
    @patch("os.path.islink", return_value=True)
    def test_symlink_to_different_canonical(self, _il, _rp) -> None:
        s = _FakeStore(sources_in_db={"u/26.04/1/foo.1.gz"})
        c = _make_classifier(
            s, inputs={"/x/u/26.04/1/foo.1.gz", "/x/u/26.04/1/canon.1.gz"}
        )
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        self.assertIsInstance(d, Symlink)
        assert isinstance(d, Symlink)
        self.assertEqual(d.canonical_source, "u/26.04/1/canon.1.gz")
        self.assertTrue(d.stale_in_db)
        self.assertTrue(d.canonical_in_inputs)

    @patch("os.path.realpath", side_effect=lambda p: "/x/u/26.04/1/canon.1.gz")
    @patch("os.path.islink", return_value=True)
    def test_symlink_canonical_not_in_inputs(self, _il, _rp) -> None:
        s = _FakeStore()
        c = _make_classifier(s, inputs={"/x/u/26.04/1/foo.1.gz"})
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        assert isinstance(d, Symlink)
        self.assertFalse(d.stale_in_db)
        self.assertFalse(d.canonical_in_inputs)

    @patch("os.path.getsize", return_value=100)
    @patch("os.path.realpath", side_effect=lambda p: p)
    @patch("os.path.islink", return_value=True)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_self_symlink_falls_through(self, _h, _il, _rp, _gs) -> None:
        # Symlink whose realpath maps to the same source path is treated as
        # a regular file (rare but possible).
        c = _make_classifier(_FakeStore())
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)


class TestClassifyFilterDb(unittest.TestCase):
    def setUp(self) -> None:
        _PATCH_SOURCE.start()
        self.addCleanup(_PATCH_SOURCE.stop)

    def _store_with_row(self, source: str, model: str) -> _FakeStore:
        meta = ExtractionMeta(model=model)
        return _FakeStore(
            extractor_info={source: ("llm", meta)},
            sources_in_db={source},
        )

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_filter_match_returns_work(self, _h, _il) -> None:
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/old")
        c = _make_classifier(s, overwrite=True, filter_specs=[("llm", "openai/old")])
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    def test_filter_no_match_returns_filter_skip(self, _il) -> None:
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/other")
        c = _make_classifier(s, overwrite=True, filter_specs=[("llm", "openai/old")])
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        self.assertIsInstance(d, FilterSkip)
        assert isinstance(d, FilterSkip)
        self.assertEqual(d.stored_model, "openai/other")

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-X")
    def test_filter_match_does_not_seed_dedup(self, _h, _il) -> None:
        # Matching filter row queues the file as Work without seeding the
        # dedup map. A later sibling with the same hash should also be Work,
        # not ContentDup, because the matching row's parsed data must be
        # refreshed end-to-end.
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/old")
        c = _make_classifier(s, overwrite=True, filter_specs=[("llm", "openai/old")])
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)
        # Sibling has same hash, same release, but is not in the DB at all.
        self.assertIsInstance(c.classify("/x/u/26.04/1/sibling.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-Y")
    def test_filter_skip_does_not_seed_dedup(self, _h, _il) -> None:
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/other")
        c = _make_classifier(s, overwrite=True, filter_specs=[("llm", "openai/old")])
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), FilterSkip)
        # Sibling with same hash should not alias onto the filter-skipped row.
        self.assertIsInstance(c.classify("/x/u/26.04/1/sibling.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_filter_with_no_db_row_falls_through(self, _h, _il) -> None:
        # File is not in the filter index → drops to dedup branch.
        s = _FakeStore(
            extractor_info={"u/26.04/1/other.1.gz": ("llm", ExtractionMeta(model="x"))}
        )
        c = _make_classifier(s, overwrite=True, filter_specs=[("llm", "openai/old")])
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_filter_matches_any_of_multiple_specs(self, _h, _il) -> None:
        # A row whose model matches the second spec is still queued as Work.
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/two")
        c = _make_classifier(
            s,
            overwrite=True,
            filter_specs=[("llm", "openai/one"), ("llm", "openai/two")],
        )
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    def test_filter_matches_none_of_multiple_specs(self, _il) -> None:
        # A row matching none of the specs is filter-skipped.
        s = self._store_with_row("u/26.04/1/foo.1.gz", "openai/three")
        c = _make_classifier(
            s,
            overwrite=True,
            filter_specs=[("llm", "openai/one"), ("llm", "openai/two")],
        )
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), FilterSkip)


class TestClassifyAlreadyStored(unittest.TestCase):
    def setUp(self) -> None:
        _PATCH_SOURCE.start()
        self.addCleanup(_PATCH_SOURCE.stop)

    @patch("os.path.islink", return_value=False)
    def test_already_stored_no_overwrite(self, _il) -> None:
        s = _FakeStore(sources_in_db={"u/26.04/1/foo.1.gz"})
        c = _make_classifier(s)
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), AlreadyStored)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="h")
    def test_overwrite_skips_already_stored_branch(self, _h, _il) -> None:
        s = _FakeStore(sources_in_db={"u/26.04/1/foo.1.gz"})
        c = _make_classifier(s, overwrite=True)
        # No filter, no dedup hit → falls through to Work.
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)


class TestClassifyContentDup(unittest.TestCase):
    def setUp(self) -> None:
        _PATCH_SOURCE.start()
        self.addCleanup(_PATCH_SOURCE.stop)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-Z")
    def test_in_run_dedup(self, _h, _il) -> None:
        c = _make_classifier(_FakeStore())
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)
        d = c.classify("/x/u/26.04/1/bar.1.gz")
        self.assertIsInstance(d, ContentDup)
        assert isinstance(d, ContentDup)
        self.assertEqual(d.canonical_source, "u/26.04/1/foo.1.gz")

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-Z")
    def test_db_seeded_dedup(self, _h, _il) -> None:
        s = _FakeStore(known_sha256s={"hash-Z": "u/26.04/1/canon.1.gz"})
        c = _make_classifier(s)
        d = c.classify("/x/u/26.04/1/foo.1.gz")
        self.assertIsInstance(d, ContentDup)
        assert isinstance(d, ContentDup)
        self.assertEqual(d.canonical_source, "u/26.04/1/canon.1.gz")

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-Z")
    def test_overwrite_drops_db_seed(self, _h, _il) -> None:
        # Same setup as above, but with --overwrite the DB seed must be skipped.
        s = _FakeStore(known_sha256s={"hash-Z": "u/26.04/1/canon.1.gz"})
        c = _make_classifier(s, overwrite=True)
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)

    @patch("os.path.islink", return_value=False)
    @patch("explainshell.extraction.common.gz_sha256", return_value="hash-Z")
    def test_dedup_scoped_by_release(self, _h, _il) -> None:
        # Same hash, different release → independent canonical, no dedup.
        c = _make_classifier(_FakeStore())
        self.assertIsInstance(c.classify("/x/u/24.04/1/foo.1.gz"), Work)
        self.assertIsInstance(c.classify("/x/u/26.04/1/foo.1.gz"), Work)


class TestApplyDecisions(unittest.TestCase):
    def test_buckets_each_decision_type(self) -> None:
        decisions = [
            Work("/p/work.gz", "u/26.04/1/work.gz"),
            SizeSkip("/p/big.gz", "u/26.04/1/big.gz", 9999, 2048, "max"),
            AlreadyStored("/p/old.gz", "u/26.04/1/old.gz"),
            FilterSkip("/p/fs.gz", "u/26.04/1/fs.gz", "llm", "openai/other"),
            ContentDup("/p/dup.gz", "u/26.04/1/dup.gz", "u/26.04/1/canon.gz"),
        ]
        s = _FakeStore()
        out = apply_decisions(decisions, s, filter_db=("llm:openai/old",))  # type: ignore[arg-type]
        self.assertEqual(out.work_files, ["/p/work.gz"])
        self.assertEqual(
            out.content_dups, [("/p/dup.gz", "u/26.04/1/dup.gz", "u/26.04/1/canon.gz")]
        )
        self.assertEqual(out.size_filtered, 1)
        self.assertEqual(out.already_stored, 1)
        # prefilter_skipped includes size + already-stored + filter-skip.
        self.assertEqual(out.prefilter_skipped, 3)
        self.assertEqual(s.deleted, [])

    def test_stale_symlink_deletes_db_row(self) -> None:
        decisions = [
            Symlink(
                gz_path="/p/sym.gz",
                short_path="u/26.04/1/sym.gz",
                canonical_source="u/26.04/1/canon.gz",
                stale_in_db=True,
                canonical_in_inputs=False,
            ),
        ]
        s = _FakeStore(sources_in_db={"u/26.04/1/sym.gz"})
        out = apply_decisions(decisions, s, filter_db=())  # type: ignore[arg-type]
        self.assertEqual(s.deleted, ["u/26.04/1/sym.gz"])
        self.assertEqual(
            out.symlinks,
            [("/p/sym.gz", "u/26.04/1/sym.gz", "u/26.04/1/canon.gz")],
        )

    def test_fresh_symlink_no_delete(self) -> None:
        decisions = [
            Symlink(
                gz_path="/p/sym.gz",
                short_path="u/26.04/1/sym.gz",
                canonical_source="u/26.04/1/canon.gz",
                stale_in_db=False,
                canonical_in_inputs=True,
            ),
        ]
        s = _FakeStore()
        apply_decisions(decisions, s, filter_db=())  # type: ignore[arg-type]
        self.assertEqual(s.deleted, [])

    def test_classified_defaults(self) -> None:
        out = apply_decisions([], _FakeStore(), filter_db=())  # type: ignore[arg-type]
        self.assertIsInstance(out, Classified)
        self.assertEqual(out.work_files, [])
        self.assertEqual(out.symlinks, [])
        self.assertEqual(out.content_dups, [])
        self.assertEqual(out.prefilter_skipped, 0)


if __name__ == "__main__":
    unittest.main()
