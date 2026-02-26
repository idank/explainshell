import pytest

from explainshell import errors
from explainshell.store import Store, ParsedManpage


@pytest.fixture
def store(tmp_path):
    """Create a Store backed by a temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    s = Store(db_path=db_path)
    yield s
    s.close()


def _make_manpage(name, section, aliases=None):
    """Helper to build a ParsedManpage with the conventional source path."""
    source = f"ubuntu/25.10/{section}/{name}.{section}.gz"
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
        store.add_manpage(grep_alias)

        # "grep" is the primary name (score 10) for this manpage
        grep_primary = _make_manpage("grep", "1", aliases=[("grep", 10)])
        store.add_manpage(grep_primary)

        results = store.find_man_page("grep")
        assert results[0].name == "grep"

    def test_higher_score_wins(self, store):
        """When multiple mappings exist for the same src, higher score wins."""
        low = _make_manpage("tool-low", "1", aliases=[("mytool", 1)])
        store.add_manpage(low)

        high = _make_manpage("tool-high", "1", aliases=[("mytool", 10)])
        store.add_manpage(high)

        results = store.find_man_page("mytool")
        assert results[0].name == "tool-high"

    def test_alias_lookup_returns_manpage(self, store):
        """Looking up a name that is only an alias should still find the manpage."""
        mp = _make_manpage("gzip", "1", aliases=[("gzip", 10), ("gunzip", 1)])
        store.add_manpage(mp)

        results = store.find_man_page("gunzip")
        assert results[0].name == "gzip"

    def test_all_candidates_returned(self, store):
        """All manpages matching the lookup name should appear in results."""
        mp1 = _make_manpage("printf", "1", aliases=[("printf", 10)])
        mp2 = _make_manpage("printf", "3", aliases=[("printf", 10)])
        store.add_manpage(mp1)
        store.add_manpage(mp2)

        results = store.find_man_page("printf")
        sections = {r.section for r in results}
        assert sections == {"1", "3"}


class TestFindManPageSection:
    def test_section_filter(self, store):
        """Specifying a section should return that section first."""
        mp1 = _make_manpage("printf", "1", aliases=[("printf", 10)])
        mp3 = _make_manpage("printf", "3", aliases=[("printf", 10)])
        store.add_manpage(mp1)
        store.add_manpage(mp3)

        results = store.find_man_page("printf.3")
        assert results[0].section == "3"

    def test_section_filter_first_result_fully_loaded(self, store):
        """The first result should be fully populated (have options data)."""
        mp = _make_manpage("printf", "1", aliases=[("printf", 10)])
        store.add_manpage(mp)

        results = store.find_man_page("printf.1")
        assert results[0].synopsis is not None

    def test_nonexistent_section_raises(self, store):
        """Requesting a section that doesn't exist should raise ProgramDoesNotExist."""
        mp = _make_manpage("printf", "1", aliases=[("printf", 10)])
        store.add_manpage(mp)

        with pytest.raises(errors.ProgramDoesNotExist):
            store.find_man_page("printf.9")

    def test_section_among_multiple(self, store):
        """When multiple sections exist, the requested one should come first."""
        mp1 = _make_manpage("open", "1", aliases=[("open", 10)])
        mp2 = _make_manpage("open", "2", aliases=[("open", 10)])
        mp3 = _make_manpage("open", "3", aliases=[("open", 10)])
        store.add_manpage(mp1)
        store.add_manpage(mp2)
        store.add_manpage(mp3)

        results = store.find_man_page("open.2")
        assert results[0].section == "2"


class TestFindManPageExactSource:
    def test_gz_lookup(self, store):
        """Looking up by .gz source path should return an exact match."""
        mp = _make_manpage("tar", "1")
        store.add_manpage(mp)

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
        store.add_manpage(mp)

        results = store.find_man_page(".")
        assert results[0].name == "."
