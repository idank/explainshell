import datetime
import logging

import pytest

from explainshell import errors
from explainshell.config import parse_distro_release
from explainshell.models import ParsedManpage, RawManpage
from explainshell.store import Store, validate_source_path


def _make_raw():
    """Build a minimal RawManpage for test use."""
    return RawManpage(
        source_text="test manpage content",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )


@pytest.fixture
def store():
    """Create a Store backed by an in-memory SQLite database."""
    s = Store.create(":memory:")
    yield s
    s.close()


def _make_manpage(name, section, aliases=None, distro="ubuntu", release="25.10"):
    """Helper to build a ParsedManpage with the conventional source path."""
    source = f"{distro}/{release}/{section}/{name}.{section}.gz"
    if aliases is None:
        aliases = [(name, 10)]
    return ParsedManpage(
        source=source,
        name=name,
        synopsis=f"{name} - do things",
        aliases=aliases,
    )


class TestFindManPageScoring:
    def test_primary_name_preferred_over_alias(self, store):
        """A manpage looked up by its primary name (score 10) should rank above
        one where the lookup name is only an alias (score 1)."""
        # "grep" is an alias (score 1) pointing to grep_alias manpage
        grep_alias = _make_manpage("grep-extra", "1", aliases=[("grep", 1)])
        store.add_manpage(grep_alias, _make_raw())

        # "grep" is the primary name (score 10) for this manpage
        grep_primary = _make_manpage("grep", "1", aliases=[("grep", 10)])
        store.add_manpage(grep_primary, _make_raw())

        results = store.find_man_page("grep")
        assert results[0].name == "grep"

    def test_higher_score_wins(self, store):
        """When multiple mappings exist for the same src, higher score wins."""
        low = _make_manpage("tool-low", "1", aliases=[("mytool", 1)])
        store.add_manpage(low, _make_raw())

        high = _make_manpage("tool-high", "1", aliases=[("mytool", 10)])
        store.add_manpage(high, _make_raw())

        results = store.find_man_page("mytool")
        assert results[0].name == "tool-high"

    def test_alias_lookup_returns_manpage(self, store):
        """Looking up a name that is only an alias should still find the manpage."""
        mp = _make_manpage("gzip", "1", aliases=[("gzip", 10), ("gunzip", 1)])
        store.add_manpage(mp, _make_raw())

        results = store.find_man_page("gunzip")
        assert results[0].name == "gzip"

    def test_all_candidates_returned(self, store):
        """All manpages matching the lookup name should appear in results."""
        mp1 = _make_manpage("printf", "1", aliases=[("printf", 10)])
        mp2 = _make_manpage("printf", "3", aliases=[("printf", 10)])
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())

        results = store.find_man_page("printf")
        sections = {r.section for r in results}
        assert sections == {"1", "3"}


class TestFindManPageSection:
    def test_section_filter(self, store):
        """Specifying a section should return that section first."""
        mp1 = _make_manpage("printf", "1", aliases=[("printf", 10)])
        mp3 = _make_manpage("printf", "3", aliases=[("printf", 10)])
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp3, _make_raw())

        results = store.find_man_page("printf.3")
        assert results[0].section == "3"

    def test_section_filter_first_result_fully_loaded(self, store):
        """The first result should be fully populated (have options data)."""
        mp = _make_manpage("printf", "1", aliases=[("printf", 10)])
        store.add_manpage(mp, _make_raw())

        results = store.find_man_page("printf.1")
        assert results[0].synopsis is not None

    def test_nonexistent_section_raises(self, store):
        """Requesting a section that doesn't exist should raise ProgramDoesNotExist."""
        mp = _make_manpage("printf", "1", aliases=[("printf", 10)])
        store.add_manpage(mp, _make_raw())

        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("printf.9")

    def test_section_among_multiple(self, store):
        """When multiple sections exist, the requested one should come first."""
        mp1 = _make_manpage("open", "1", aliases=[("open", 10)])
        mp2 = _make_manpage("open", "2", aliases=[("open", 10)])
        mp3 = _make_manpage("open", "3", aliases=[("open", 10)])
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())
        store.add_manpage(mp3, _make_raw())

        results = store.find_man_page("open.2")
        assert results[0].section == "2"


class TestFindManPageExactSource:
    def test_gz_lookup(self, store):
        """Looking up by .gz source path should return an exact match."""
        mp = _make_manpage("tar", "1")
        store.add_manpage(mp, _make_raw())

        results = store.find_man_page("ubuntu/25.10/1/tar.1.gz")
        assert results[0].name == "tar"

    def test_gz_lookup_not_found_raises(self, store):
        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("nonexistent.1.gz")


class TestFindManPageNotFound:
    def test_unknown_name_raises(self, store):
        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("nosuchprogram")

    def test_dot_command(self, store):
        """The '.' command (source) should be looked up without splitting on dot."""
        mp = ParsedManpage(
            source="ubuntu/25.10/1/..1.gz",
            name=".",
            synopsis=". - source a file",
            aliases=[(".", 10)],
        )
        store.add_manpage(mp, _make_raw())

        results = store.find_man_page(".")
        assert results[0].name == "."


class TestHasManpageSource:
    def test_returns_true_for_existing_source(self, store):
        mp = _make_manpage("tar", "1")
        store.add_manpage(mp, _make_raw())

        assert store.has_manpage_source("ubuntu/25.10/1/tar.1.gz") is True

    def test_returns_false_for_missing_source(self, store):
        assert store.has_manpage_source("ubuntu/25.10/1/missing.1.gz") is False


class TestParseDistroRelease:
    def test_distro_path(self):
        assert parse_distro_release("ubuntu/25.10/1/ps.1.gz") == ("ubuntu", "25.10")

    def test_different_distro(self):
        assert parse_distro_release("debian/12/8/foo.8.gz") == ("debian", "12")


class TestAddManpageDuplicatePrevention:
    def test_same_source_reimport_succeeds(self, store):
        """Re-importing the same source should replace the old entry."""
        mp = _make_manpage("ps", "1")
        store.add_manpage(mp, _make_raw())
        store.add_manpage(mp, _make_raw())  # should not raise

    def test_same_name_section_distro_different_source_raises(self, store):
        """Two manpages with same name+section+distro but different source should raise."""
        mp1 = ParsedManpage(
            source="ubuntu/25.10/1/ps.1.gz",
            name="ps",
            synopsis="ps - report",
            aliases=[("ps", 10)],
        )
        mp2 = ParsedManpage(
            source="ubuntu/25.10/1/procps-ps.1.gz",
            name="ps",
            synopsis="ps - report processes",
            aliases=[("ps", 10)],
        )
        store.add_manpage(mp1, _make_raw())
        with pytest.raises(errors.DuplicateManpage):
            store.add_manpage(mp2, _make_raw())

    def test_same_name_different_distro_succeeds(self, store):
        """Same name+section in different distros should be allowed."""
        mp1 = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        mp2 = _make_manpage("ps", "1", distro="debian", release="12")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())  # should not raise

    def test_same_name_different_section_succeeds(self, store):
        """Same name in different sections within same distro should be allowed."""
        mp1 = _make_manpage("printf", "1")
        mp3 = _make_manpage("printf", "3")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp3, _make_raw())  # should not raise


class TestFindManPageDistroScoping:
    def test_filter_by_distro(self, store):
        """find_man_page with distro/release should only return matching manpages."""
        mp_ubuntu = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        mp_debian = _make_manpage("ps", "1", distro="debian", release="12")
        store.add_manpage(mp_ubuntu, _make_raw())
        store.add_manpage(mp_debian, _make_raw())

        results = store.find_man_page("ps", distro="ubuntu", release="25.10")
        assert len(results) == 1
        assert results[0].source.startswith("ubuntu/25.10/")

    def test_no_results_raises(self, store):
        """Filtering by a distro that has no matching manpage should raise."""
        mp = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        store.add_manpage(mp, _make_raw())

        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("ps", distro="debian", release="12")

    def test_no_filter_returns_all(self, store):
        """Without distro/release, all matching manpages should be returned."""
        mp_ubuntu = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        mp_debian = _make_manpage("ps", "1", distro="debian", release="12")
        store.add_manpage(mp_ubuntu, _make_raw())
        store.add_manpage(mp_debian, _make_raw())

        results = store.find_man_page("ps")
        assert len(results) == 2


class TestDistros:
    def test_returns_distro_release_pairs(self, store):
        mp1 = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        mp2 = _make_manpage("ls", "1", distro="debian", release="12")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())

        pairs = store.distros()
        assert ("ubuntu", "25.10") in pairs
        assert ("debian", "12") in pairs

    def test_no_duplicates(self, store):
        mp1 = _make_manpage("ps", "1", distro="ubuntu", release="25.10")
        mp2 = _make_manpage("ls", "1", distro="ubuntu", release="25.10")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())

        pairs = store.distros()
        assert pairs.count(("ubuntu", "25.10")) == 1


class TestGetManpageSource:
    def test_returns_text_and_generator(self, store):
        mp = _make_manpage("tar", "1")
        raw = RawManpage(
            source_text=".TH TAR 1",
            generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            generator="roff",
        )
        store.add_manpage(mp, raw)

        result = store.get_raw_manpage("ubuntu/25.10/1/tar.1.gz")
        assert result is not None
        assert result.source_text == ".TH TAR 1"
        assert result.generator == "roff"

    def test_markdown_generator(self, store):
        mp = _make_manpage("curl", "1")
        raw = RawManpage(
            source_text="# curl",
            generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            generator="mandoc -T markdown",
        )
        store.add_manpage(mp, raw)

        result = store.get_raw_manpage("ubuntu/25.10/1/curl.1.gz")
        assert result is not None
        assert "markdown" in result.generator

    def test_not_found_returns_none(self, store):
        assert store.get_raw_manpage("ubuntu/25.10/1/nosuch.1.gz") is None


class TestListSections:
    def test_returns_distinct_sections(self, store):
        mp1 = _make_manpage("tar", "1")
        mp3 = _make_manpage("printf", "3")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp3, _make_raw())

        sections = store.list_sections("ubuntu", "25.10")
        assert sections == ["1", "3"]

    def test_no_duplicates(self, store):
        mp1 = _make_manpage("tar", "1")
        mp2 = _make_manpage("ls", "1")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())

        sections = store.list_sections("ubuntu", "25.10")
        assert sections == ["1"]

    def test_empty_for_unknown_distro(self, store):
        mp = _make_manpage("tar", "1")
        store.add_manpage(mp, _make_raw())

        assert store.list_sections("debian", "12") == []


class TestListManpages:
    def test_prefix_filters_by_distro_release(self, store):
        mp1 = _make_manpage("tar", "1", distro="ubuntu", release="25.10")
        mp2 = _make_manpage("ps", "1", distro="debian", release="12")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp2, _make_raw())

        sources = store.list_manpages("ubuntu/25.10/")
        assert "ubuntu/25.10/1/tar.1.gz" in sources
        assert "debian/12/1/ps.1.gz" not in sources

    def test_prefix_filters_by_section(self, store):
        mp1 = _make_manpage("printf", "1")
        mp3 = _make_manpage("printf", "3")
        store.add_manpage(mp1, _make_raw())
        store.add_manpage(mp3, _make_raw())

        sources = store.list_manpages("ubuntu/25.10/3/")
        assert sources == ["ubuntu/25.10/3/printf.3.gz"]

    def test_empty_result(self, store):
        mp = _make_manpage("tar", "1")
        store.add_manpage(mp, _make_raw())

        assert store.list_manpages("debian/12/") == []


class TestValidateSourcePath:
    def test_valid_path(self):
        validate_source_path("ubuntu/25.10/1/tar.1.gz")

    def test_valid_path_section_8(self):
        validate_source_path("debian/12/8/iptables.8.gz")

    def test_bare_basename_rejected(self):
        with pytest.raises(errors.InvalidSourcePath):
            validate_source_path("tar.1.gz")

    def test_missing_distro_rejected(self):
        with pytest.raises(errors.InvalidSourcePath):
            validate_source_path("25.10/1/tar.1.gz")

    def test_add_manpage_rejects_bare_source(self, store):
        mp = ParsedManpage(
            source="tar.1.gz",
            name="tar",
            synopsis="tar - archiver",
            aliases=[("tar", 10)],
        )
        with pytest.raises(errors.InvalidSourcePath):
            store.add_manpage(mp, _make_raw())


class _SubcommandTestBase:
    """Shared helpers for subcommand mapping tests."""

    def _make_mp(
        self,
        name: str,
        section: str = "1",
        *,
        extractor: str | None = None,
        subcommands: list[str] | None = None,
    ) -> ParsedManpage:
        source = f"ubuntu/25.10/{section}/{name}.{section}.gz"
        return ParsedManpage(
            source=source,
            name=name,
            synopsis=f"{name} - do things",
            aliases=[(name, 10)],
            extractor=extractor,
            subcommands=subcommands or [],
        )

    def _get_mappings(self, store: Store) -> dict[str, str]:
        """Return {src: dst} for all mappings in the store."""
        return {src: dst for src, dst in store.mappings()}

    def _get_subcommands(self, store: Store, source: str) -> list[str]:
        """Read the subcommands list for a manpage from the DB."""
        import json

        row = store._conn.execute(
            "SELECT subcommands FROM parsed_manpages WHERE source = ?", (source,)
        ).fetchone()
        return json.loads(row["subcommands"]) if row else []


class TestUpdateSubcommandMappingsHeuristic(_SubcommandTestBase):
    """Tests for update_subcommand_mappings_heuristic()."""

    def test_creates_mapping_for_hyphenated_child(self, store):
        """git + git-commit → mapping 'git commit' -> git-commit source."""
        store.add_manpage(self._make_mp("git", extractor="source"), _make_raw())
        store.add_manpage(self._make_mp("git-commit", extractor="source"), _make_raw())

        mappings_added, parents = store.update_subcommand_mappings_heuristic()

        assert ("git commit", "ubuntu/25.10/1/git-commit.1.gz") in mappings_added
        assert "git" in parents

    def test_sets_subcommands_on_parent(self, store):
        """Heuristic path should set the subcommands list on the parent."""
        store.add_manpage(self._make_mp("git", extractor="source"), _make_raw())
        store.add_manpage(self._make_mp("git-commit", extractor="source"), _make_raw())
        store.add_manpage(self._make_mp("git-push", extractor="source"), _make_raw())

        store.update_subcommand_mappings_heuristic()

        subs = self._get_subcommands(store, "ubuntu/25.10/1/git.1.gz")
        assert sorted(subs) == ["commit", "push"]

    def test_no_mapping_without_parent(self, store):
        """A hyphenated name with no matching parent should not create a mapping."""
        store.add_manpage(self._make_mp("cd-discid", extractor="source"), _make_raw())

        mappings_added, parents = store.update_subcommand_mappings_heuristic()

        assert mappings_added == []
        assert parents == {}

    def test_skips_existing_mappings(self, store):
        """Should not duplicate mappings that already exist."""
        store.add_manpage(self._make_mp("git", extractor="source"), _make_raw())
        store.add_manpage(self._make_mp("git-commit", extractor="source"), _make_raw())

        store.update_subcommand_mappings_heuristic()
        # Run a second time — should add nothing new.
        mappings_added, _ = store.update_subcommand_mappings_heuristic()

        assert mappings_added == []


class TestUpdateSubcommandMappingsLlm(_SubcommandTestBase):
    """Tests for update_subcommand_mappings_llm()."""

    def test_uses_declared_subcommands(self, store):
        """LLM parent with subcommands=['commit','push'] creates mappings
        only for children that exist as manpages."""
        store.add_manpage(
            self._make_mp(
                "git", extractor="llm", subcommands=["commit", "push", "stash"]
            ),
            _make_raw(),
        )
        store.add_manpage(self._make_mp("git-commit", extractor="llm"), _make_raw())
        store.add_manpage(self._make_mp("git-push", extractor="llm"), _make_raw())
        # git-stash does NOT exist — should be silently skipped.

        mappings_added, parents = store.update_subcommand_mappings_llm()

        srcs = {src for src, _ in mappings_added}
        assert "git commit" in srcs
        assert "git push" in srcs
        assert "git stash" not in srcs
        assert "git" in parents

    def test_does_not_create_false_positive(self, store):
        """LLM parent without the child in its subcommands list should not
        create a mapping even if the hyphenated name exists."""
        store.add_manpage(
            self._make_mp("python", extractor="llm", subcommands=[]),
            _make_raw(),
        )
        store.add_manpage(
            self._make_mp("python-socketio", extractor="llm"), _make_raw()
        )

        mappings_added, parents = store.update_subcommand_mappings_llm()

        srcs = {src for src, _ in mappings_added}
        assert "python socketio" not in srcs

    def test_does_not_set_subcommands_on_parent(self, store):
        """LLM parents already have their subcommands; the method should not
        overwrite them."""
        store.add_manpage(
            self._make_mp("git", extractor="llm", subcommands=["commit"]),
            _make_raw(),
        )
        store.add_manpage(self._make_mp("git-commit", extractor="llm"), _make_raw())

        store.update_subcommand_mappings_llm()

        subs = self._get_subcommands(store, "ubuntu/25.10/1/git.1.gz")
        assert subs == ["commit"]  # unchanged from the original value

    def test_skips_existing_mappings(self, store):
        """LLM path should not duplicate mappings on re-run."""
        store.add_manpage(
            self._make_mp("git", extractor="llm", subcommands=["commit"]),
            _make_raw(),
        )
        store.add_manpage(self._make_mp("git-commit", extractor="llm"), _make_raw())

        store.update_subcommand_mappings_llm()
        mappings_added, _ = store.update_subcommand_mappings_llm()

        assert mappings_added == []

    def test_multi_distro_creates_per_distro_mappings(self, store):
        """Subcommand mappings should be created per distro, not cross-distro."""

        def mp(name: str, distro: str, release: str, **kwargs) -> ParsedManpage:
            source = f"{distro}/{release}/1/{name}.1.gz"
            return ParsedManpage(
                source=source,
                name=name,
                synopsis=f"{name} - do things",
                aliases=[(name, 10)],
                **kwargs,
            )

        # Two distros, each with gh + gh-cache.
        store.add_manpage(
            mp("gh", "ubuntu", "26.04", extractor="llm", subcommands=["cache"]),
            _make_raw(),
        )
        store.add_manpage(
            mp("gh-cache", "ubuntu", "26.04", extractor="llm"), _make_raw()
        )
        store.add_manpage(
            mp("gh", "arch", "latest", extractor="llm", subcommands=["cache"]),
            _make_raw(),
        )
        store.add_manpage(
            mp("gh-cache", "arch", "latest", extractor="llm"), _make_raw()
        )

        mappings_added, _ = store.update_subcommand_mappings_llm()

        dsts = {dst for _, dst in mappings_added}
        assert "ubuntu/26.04/1/gh-cache.1.gz" in dsts
        assert "arch/latest/1/gh-cache.1.gz" in dsts
        # Each mapping should point to its own distro's child.
        mapping_pairs = {(src, dst) for src, dst in mappings_added}
        assert ("gh cache", "ubuntu/26.04/1/gh-cache.1.gz") in mapping_pairs
        assert ("gh cache", "arch/latest/1/gh-cache.1.gz") in mapping_pairs
        # No cross-distro mappings.
        assert ("gh cache", "arch/latest/1/gh-cache.1.gz") not in {
            (src, dst)
            for src, dst in mappings_added
            if src == "gh cache" and "ubuntu" in dst and "arch" in dst
        }


class TestReimportWarnsAboutLostMappings:
    """Verify that re-importing a canonical warns about non-alias mappings lost to CASCADE."""

    def test_reimport_warns_about_lost_symlink_mapping(self, store, caplog):
        """Re-importing a canonical logs a warning about lost non-alias mappings."""
        mp = _make_manpage("bio-eagle", "1", aliases=[("bio-eagle", 10)])
        raw = _make_raw()
        store.add_manpage(mp, raw)

        # Simulate a symlink-derived mapping added by the manager.
        store.add_mapping("eagle", mp.source, score=10)
        assert store.has_mapping("eagle", mp.source)

        # Re-import the same manpage (simulates --overwrite).
        with caplog.at_level(logging.WARNING):
            mp2 = _make_manpage("bio-eagle", "1", aliases=[("bio-eagle", 10)])
            store.add_manpage(mp2, raw)

        # The symlink mapping is gone (CASCADE).
        assert not store.has_mapping("eagle", mp2.source)
        # A warning was logged.
        assert any(
            "eagle" in r.message and "non-alias" in r.message for r in caplog.records
        )

    def test_reimport_no_warning_when_only_aliases(self, store, caplog):
        """Re-importing when all mappings are aliases produces no warning."""
        mp = _make_manpage("bio-eagle", "1", aliases=[("bio-eagle", 10)])
        raw = _make_raw()
        store.add_manpage(mp, raw)

        with caplog.at_level(logging.WARNING):
            mp2 = _make_manpage("bio-eagle", "1", aliases=[("bio-eagle", 10)])
            store.add_manpage(mp2, raw)

        assert not any("non-alias" in r.message for r in caplog.records)

    def test_mapping_score_and_update(self, store):
        """mapping_score returns the score, update_mapping_score changes it."""
        mp = _make_manpage("bio-eagle", "1", aliases=[("bio-eagle", 10), ("eagle", 1)])
        raw = _make_raw()
        store.add_manpage(mp, raw)

        assert store.mapping_score("eagle", mp.source) == 1
        store.update_mapping_score("eagle", mp.source, score=10)
        assert store.mapping_score("eagle", mp.source) == 10
