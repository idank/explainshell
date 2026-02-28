import pytest

from explainshell.store import Store, ParsedManpage
from tools.db_check import check as db_check


def _make_manpage(name, section, aliases=None, distro="ubuntu", release="25.10"):
    source = f"{distro}/{release}/{section}/{name}.{section}.gz"
    if aliases is None:
        aliases = [(name, 10)]
    return ParsedManpage(
        source=source,
        name=name,
        synopsis=f"{name} - do things",
        aliases=aliases,
    )


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def store(db_path):
    s = Store(db_path=db_path)
    yield s
    s.close()


class TestCheck:
    def test_clean_db(self, store, db_path):
        store.add_manpage(_make_manpage("tar", "1"))
        assert db_check(db_path) == []

    def test_malformed_source(self, store, db_path):
        """Directly insert a row with a bare source to test the checker."""
        store._conn.execute(
            "INSERT INTO manpage(source, name, options, aliases) VALUES (?, ?, '[]', '[]')",
            ("tar.1.gz", "tar"),
        )
        store._conn.commit()
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("malformed source path" in msg for msg in errors_list)

    def test_orphaned_mapping(self, store, db_path):
        """Insert a mapping pointing to a non-existent manpage."""
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO mapping(src, dst, score) VALUES (?, ?, ?)",
            ("ghost", 99999, 10),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("orphaned mapping" in msg for msg in errors_list)

    def test_unreachable_manpage(self, store, db_path):
        """A manpage with no mappings should be flagged as a warning."""
        store._conn.execute(
            "INSERT INTO manpage(source, name, options, aliases) "
            "VALUES (?, ?, '[]', '[]')",
            ("ubuntu/25.10/1/lonely.1.gz", "lonely"),
        )
        store._conn.commit()
        issues = db_check(db_path)
        warnings = [msg for sev, msg in issues if sev == "warning"]
        assert any("unreachable manpage" in msg for msg in warnings)

    def test_shadowed_duplicate(self, store, db_path):
        """Two manpages with same name+section+distro should be flagged."""
        store.add_manpage(_make_manpage("ps", "1"))
        # Directly insert a second one with same name/section/distro but different source
        store._conn.execute(
            "INSERT INTO manpage(source, name, options, aliases) "
            "VALUES (?, ?, '[]', '[]')",
            ("ubuntu/25.10/1/procps-ps.1.gz", "ps"),
        )
        store._conn.commit()
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("shadowed duplicate" in msg for msg in errors_list)
