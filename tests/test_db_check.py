import datetime
import json

import pytest

from explainshell.models import ParsedManpage, RawManpage
from explainshell.store import Store
from explainshell.db_check import check as db_check


def _make_raw():
    """Build a minimal RawManpage for test use."""
    return RawManpage(
        source_text="test manpage content",
        generated_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        generator="test",
    )


def _make_manpage(name, section, aliases=None, distro="ubuntu", release="26.04"):
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
    s = Store.create(db_path)
    yield s
    s.close()


class TestCheck:
    def test_clean_db(self, store, db_path):
        store.add_manpage(_make_manpage("tar", "1"), _make_raw())
        assert db_check(db_path) == []

    def test_malformed_source(self, store, db_path):
        """Directly insert a row with a bare source to test the checker."""
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO parsed_manpages(source, name, options, aliases) VALUES (?, ?, '[]', '[]')",
            ("tar.1.gz", "tar"),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("malformed source path" in msg for msg in errors_list)

    def test_orphaned_mapping(self, store, db_path):
        """Insert a mapping pointing to a non-existent manpage."""
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("ghost", "ubuntu/26.04/1/ghost.1.gz", 10),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("orphaned mapping" in msg for msg in errors_list)

    def test_unreachable_manpage(self, store, db_path):
        """A manpage with no mappings should be flagged as a warning."""
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO parsed_manpages(source, name, options, aliases) "
            "VALUES (?, ?, '[]', '[]')",
            ("ubuntu/26.04/1/lonely.1.gz", "lonely"),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        warnings = [msg for sev, msg in issues if sev == "warning"]
        assert any("unreachable manpage" in msg for msg in warnings)

    def test_shadowed_duplicate(self, store, db_path):
        """Two manpages with same name+section+distro should be flagged."""
        store.add_manpage(_make_manpage("ps", "1"), _make_raw())
        # Directly insert a second one with same name/section/distro but different source
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO parsed_manpages(source, name, options, aliases) "
            "VALUES (?, ?, '[]', '[]')",
            ("ubuntu/26.04/1/procps-ps.1.gz", "ps"),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any("shadowed duplicate" in msg for msg in errors_list)

    def test_positional_on_flagged_option(self, store, db_path):
        """Options with short/long flags should not have positional set."""
        opts = json.dumps(
            [
                {
                    "text": "-D debugopts desc",
                    "short": ["-D"],
                    "long": [],
                    "has_argument": True,
                    "positional": "debugopts",
                    "nested_cmd": False,
                }
            ]
        )
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO parsed_manpages(source, name, options, aliases) "
            "VALUES (?, ?, ?, '[]')",
            ("ubuntu/26.04/1/find.1.gz", "find", opts),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        warnings = [msg for sev, msg in issues if sev == "warning"]
        assert any("positional on flagged option" in msg for msg in warnings)

    def test_positional_on_positional_ok(self, store, db_path):
        """Positional operands (no flags) with positional set should not warn."""
        opts = json.dumps(
            [
                {
                    "text": "FILE desc",
                    "short": [],
                    "long": [],
                    "has_argument": False,
                    "positional": "FILE",
                    "nested_cmd": False,
                }
            ]
        )
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO parsed_manpages(source, name, options, aliases) "
            "VALUES (?, ?, ?, '[]')",
            ("ubuntu/26.04/1/cat.1.gz", "cat", opts),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        warnings = [msg for sev, msg in issues if sev == "warning"]
        assert not any("argument on flagged option" in msg for msg in warnings)

    def test_stale_subcommand_mapping_missing_parent(self, store, db_path):
        """Subcommand mapping whose parent doesn't exist should be an error."""
        store.add_manpage(_make_manpage("git-commit", "1"), _make_raw())
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("git commit", "ubuntu/26.04/1/git-commit.1.gz", 1),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        errors_list = [msg for sev, msg in issues if sev == "error"]
        assert any(
            "stale subcommand mapping" in msg and "does not exist" in msg
            for msg in errors_list
        )

    def test_stale_subcommand_mapping_not_declared(self, store, db_path):
        """Subcommand mapping where parent doesn't declare the subcommand should warn."""
        store.add_manpage(_make_manpage("cd", "1"), _make_raw())
        store.add_manpage(_make_manpage("cd-discid", "1"), _make_raw())
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("cd discid", "ubuntu/26.04/1/cd-discid.1.gz", 1),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        warnings = [msg for sev, msg in issues if sev == "warning"]
        assert any(
            "stale subcommand mapping" in msg and "does not declare" in msg
            for msg in warnings
        )

    def test_valid_subcommand_mapping_ok(self, store, db_path):
        """Subcommand mapping matching parent's declared subcommands should not warn."""
        mp = ParsedManpage(
            source="ubuntu/26.04/1/git.1.gz",
            name="git",
            synopsis="git - version control",
            aliases=[("git", 10)],
            subcommands=["commit"],
        )
        store.add_manpage(mp, _make_raw())
        store.add_manpage(_make_manpage("git-commit", "1"), _make_raw())
        store._conn.execute("PRAGMA foreign_keys = OFF")
        store._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            ("git commit", "ubuntu/26.04/1/git-commit.1.gz", 1),
        )
        store._conn.commit()
        store._conn.execute("PRAGMA foreign_keys = ON")
        issues = db_check(db_path)
        assert not any("stale subcommand mapping" in msg for _, msg in issues)
