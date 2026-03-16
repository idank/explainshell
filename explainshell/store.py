"""
data objects to save processed man pages to sqlite
"""

import collections
import dataclasses
import datetime
import json
import logging
import os
import re
import sqlite3
import zlib

from pydantic import BaseModel

from explainshell import errors, help_constants, util, config

logger = logging.getLogger(__name__)

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS manpages (
    source             TEXT    PRIMARY KEY,
    data               BLOB   NOT NULL,
    generated_at       TEXT   NOT NULL,
    generator          TEXT   NOT NULL,
    generator_version  TEXT,
    source_gz_sha256   TEXT
);

CREATE TABLE IF NOT EXISTS parsed_manpages (
    source        TEXT    PRIMARY KEY,            -- e.g. "ubuntu/25.10/1/tar.1.gz"
    name          TEXT    NOT NULL,               -- command name (e.g. 'git')
    synopsis      TEXT,                           -- one-line synopsis from the man page
    options       TEXT    NOT NULL DEFAULT '[]',  -- JSON list of option dicts
    aliases       TEXT    NOT NULL DEFAULT '[]',  -- JSON list of [alias, score] pairs
    dashless_opts INTEGER NOT NULL DEFAULT 0,      -- allow matching options without leading '-'
    has_subcommands INTEGER NOT NULL DEFAULT 0,   -- command has sub-commands; matcher looks ahead for "git commit" -> git-commit manpage
    updated       INTEGER NOT NULL DEFAULT 0,     -- manually edited, skip during bulk imports
    nested_cmd    TEXT    NOT NULL DEFAULT 'false', -- positional args start a nested command (e.g. sudo, xargs)
    extractor     TEXT,                            -- extractor mode: "source", "mandoc", "llm"
    extraction_meta TEXT NOT NULL DEFAULT '{}',    -- JSON dict of additional extraction metadata
    FOREIGN KEY (source) REFERENCES manpages(source) ON DELETE CASCADE
);

-- Maps command names (and aliases) to parsed_manpages rows.
-- A single manpage may have many mappings (one per alias).
-- For multi-cmd parents, sub-command mappings are also stored here
-- (e.g. src='git commit' -> dst=<git-commit manpage id>).
CREATE TABLE IF NOT EXISTS mappings (
    id    INTEGER PRIMARY KEY,
    src   TEXT    NOT NULL,      -- lookup key (command name or 'cmd subcmd')
    dst   TEXT    NOT NULL REFERENCES parsed_manpages(source) ON DELETE CASCADE,
    score INTEGER NOT NULL       -- higher score = preferred match
);

CREATE INDEX IF NOT EXISTS idx_mappings_src ON mappings(src);
CREATE INDEX IF NOT EXISTS idx_mappings_dst ON mappings(dst);

"""


def _compress(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"))


def _decompress(data: bytes) -> str:
    return zlib.decompress(data).decode("utf-8")


@dataclasses.dataclass
class RawManpage:
    source_text: str
    generated_at: datetime.datetime
    generator: str
    generator_version: str | None = None
    source_gz_sha256: str | None = None


_SOURCE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*/[^/]+/[^/]+/[^/]+\.\d\w*\.gz$")


def validate_source_path(source):
    """Validate that *source* has the ``distro/release/section/name.section.gz`` format.

    Raises ``errors.InvalidSourcePath`` on failure.
    """
    if not _SOURCE_RE.match(source):
        raise errors.InvalidSourcePath(
            f"source path {source!r} does not match the required "
            f"'distro/release/section/name.section.gz' format "
            f"(e.g. 'ubuntu/25.10/1/tar.1.gz')"
        )


class Option(BaseModel):
    """An extracted command-line option from a man page.

    short - a list of short options (-a, -b, ..)
    long - a list of long options (--a, --b)
    has_argument - specifies if one of the short/long options expects an additional argument
    positional - specifies if to consider this as positional arguments
    nested_cmd - specifies if the arguments to this option can start a nested command
    """

    text: str
    short: list[str] = []
    long: list[str] = []
    has_argument: bool | list[str] = False
    positional: str | bool | None = None
    nested_cmd: bool | list[str] = False
    meta: dict | None = None

    @property
    def opts(self) -> list[str]:
        return self.short + self.long

    def __str__(self):
        return "(" + ", ".join([str(x) for x in self.opts]) + ")"

    def __repr__(self):
        return f"<option {self}>"


class ParsedManpage(BaseModel):
    """processed man page

    source - the path to the original source man page
    name - the name of this man page as extracted by manpage.manpage
    synopsis - the synopsis of this man page as extracted by manpage.manpage
    options - a list of options extracted from this man page
    aliases - a list of aliases found for this man page
    dashless_opts - allow interpreting options without a leading '-'
    has_subcommands - command has sub-commands; when set, the matcher looks ahead for
        e.g. "git commit" and resolves it to the git-commit manpage
    updated - whether this man page was manually updated
    nested_cmd - specifies if positional arguments to this program can start a nested command,
        e.g. sudo, xargs
    """

    source: str
    name: str
    synopsis: str | None = None
    options: list[Option] = []
    aliases: list[tuple[str, int]] = []
    dashless_opts: bool = False
    has_subcommands: bool = False
    updated: bool = False
    nested_cmd: bool | str = False
    extractor: str | None = None
    extraction_meta: dict | None = None

    @property
    def name_section(self):
        name, section = util.name_section(os.path.basename(self.source)[:-3])
        return f"{name}({section})"

    @property
    def section(self):
        name, section = util.name_section(os.path.basename(self.source)[:-3])
        return section

    @property
    def positionals(self):
        # go over all options and look for those with the same 'positional' field
        groups = collections.OrderedDict()
        for opt in self.options:
            if opt.positional:
                groups.setdefault(opt.positional, []).append(opt)

        # merge all the options under the same argument to a single string
        for k, ln in groups.items():
            groups[k] = "\n\n".join([p.text for p in ln])

        return groups

    def find_option(self, flag):
        for o_tmp in self.options:
            for o in o_tmp.opts:
                if o == flag:
                    return o_tmp

    def to_store(self):
        return {
            "source": self.source,
            "name": self.name,
            "synopsis": self.synopsis,
            "options": json.dumps([o.model_dump() for o in self.options]),
            "aliases": json.dumps(self.aliases),
            "dashless_opts": int(bool(self.dashless_opts)),
            "has_subcommands": int(bool(self.has_subcommands)),
            "updated": int(bool(self.updated)),
            "nested_cmd": json.dumps(self.nested_cmd),
            "extractor": self.extractor,
            "extraction_meta": json.dumps(self.extraction_meta or {}),
        }

    @staticmethod
    def from_store(d):
        options = []
        for od in json.loads(d["options"]):
            options.append(Option.model_validate(od))

        synopsis = d["synopsis"]
        if not synopsis:
            synopsis = help_constants.NO_SYNOPSIS

        dashless_opts = bool(d["dashless_opts"])
        has_subcommands = bool(d["has_subcommands"])
        nested_cmd = json.loads(d["nested_cmd"])

        extraction_meta_raw = d["extraction_meta"]
        extraction_meta = json.loads(extraction_meta_raw) if extraction_meta_raw else {}

        return ParsedManpage(
            source=d["source"],
            name=d["name"],
            synopsis=synopsis,
            options=options,
            aliases=[tuple(x) for x in json.loads(d["aliases"])],
            dashless_opts=dashless_opts,
            has_subcommands=has_subcommands,
            updated=bool(d["updated"]),
            nested_cmd=nested_cmd,
            extractor=d["extractor"],
            extraction_meta=extraction_meta or None,
        )

    def __repr__(self):
        return f"<manpage {self.name}({self.section}), {len(self.options)} options>"


class Store:
    """read/write processed man pages from sqlite"""

    def __init__(self, db_path, read_only=False):
        logger.info("creating store, db_path = %r, read_only = %s", db_path, read_only)
        # check_same_thread=False: the default sqlite3 driver raises if a
        # connection is used from a thread other than the one that created it.
        # Flask serves requests from multiple threads sharing a single Store,
        # but each request does independent read-only queries so this is safe.
        if read_only:
            self._conn = sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def create(cls, db_path):
        """Create a new (or open an existing) writable database and return a Store."""
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.executescript(_CREATE_SCHEMA)
        conn.close()
        return cls(db_path)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def drop(self, confirm=False):
        if not confirm:
            return

        logger.info("dropping mappings, parsed_manpages, manpages tables")
        self._conn.executescript("""
            DELETE FROM mappings;
            DELETE FROM parsed_manpages;
            DELETE FROM manpages;
        """)
        self._conn.commit()

    def find_man_page(self, name, distro=None, release=None):
        """find a man page by its name, everything following the last dot (.) in name,
        is taken as the section of the man page

        we return the man page found with the highest score, and a list of
        suggestions that also matched the given name (only the first item
        is prepopulated with the option data)

        when distro and release are set, filter results to manpages whose
        source starts with ``distro/release/``."""
        if name.endswith(".gz"):
            logger.info("name ends with .gz, looking up an exact match by source")
            row = self._conn.execute(
                "SELECT * FROM parsed_manpages WHERE source = ?", (name,)
            ).fetchone()
            if not row:
                raise errors.ProgramDoesNotExist(name)
            m = ParsedManpage.from_store(dict(row))
            logger.info("returning %s", m)
            return [m]

        section = None
        orig_name = name

        # don't try to look for a section if it's . (source)
        if name != ".":
            splitted = name.rsplit(".", 1)
            name = splitted[0]
            if len(splitted) > 1:
                section = splitted[1]

        logger.info("looking up manpage in mappings with src %r", name)
        mapping_rows = self._conn.execute(
            "SELECT dst, score FROM mappings WHERE src = ?", (name,)
        ).fetchall()

        if not mapping_rows:
            raise errors.ProgramDoesNotExist(name)

        dsts = {row["dst"]: row["score"] for row in mapping_rows}

        placeholders = ",".join("?" * len(dsts))
        manpage_rows = self._conn.execute(
            f"SELECT name, source FROM parsed_manpages WHERE source IN ({placeholders})",
            list(dsts.keys()),
        ).fetchall()

        if len(manpage_rows) != len(dsts):
            logger.error(
                "one of %r mappings is missing in parsed_manpages table "
                "(%d mappings, %d found)",
                dsts,
                len(dsts),
                len(manpage_rows),
            )

        # Apply distro/release filter when requested
        if distro is not None and release is not None:
            prefix = f"{distro}/{release}/"
            manpage_rows = [
                row for row in manpage_rows if row["source"].startswith(prefix)
            ]
            if not manpage_rows:
                raise errors.ProgramDoesNotExist(name)
            # Rebuild dsts to only include filtered rows
            dsts = {row["source"]: dsts[row["source"]] for row in manpage_rows}

        results = [
            (row["source"], ParsedManpage(source=row["source"], name=row["name"]))
            for row in manpage_rows
        ]
        results.sort(key=lambda x: dsts.get(x[0], 0), reverse=True)
        logger.info(
            "found %d candidates: %s",
            len(results),
            [(src, m.name_section) for src, m in results],
        )

        if section is not None:
            if len(results) > 1:
                results.sort(
                    key=lambda src_m: src_m[1].section == section, reverse=True
                )
                logger.info("sorted candidates so section %s is first", section)
            if results[0][1].section != section:
                raise errors.ProgramDoesNotExist(orig_name)
            results.extend(
                self._discover_manpage_suggestions(
                    results[0][0],
                    results,
                    distro=distro,
                    release=release,
                )
            )

        top_source = results[0][0]
        results = [x[1] for x in results]
        row = self._conn.execute(
            "SELECT * FROM parsed_manpages WHERE source = ?", (top_source,)
        ).fetchone()
        results[0] = ParsedManpage.from_store(dict(row))
        return results

    def _discover_manpage_suggestions(
        self, source, existing, distro=None, release=None
    ):
        """find suggestions for a given man page

        source is the source path of the man page in question,
        existing is a list of (source, man page) of suggestions that were
        already discovered
        """
        skip = {src for src, m in existing}

        # find all srcs that point to this source
        src_rows = self._conn.execute(
            "SELECT src FROM mappings WHERE dst = ?", (source,)
        ).fetchall()
        srcs = [row["src"] for row in src_rows]
        if not srcs:
            return []

        # find all dsts of those srcs
        placeholders = ",".join("?" * len(srcs))
        dst_rows = self._conn.execute(
            f"SELECT DISTINCT dst FROM mappings WHERE src IN ({placeholders})",
            srcs,
        ).fetchall()
        suggestion_sources = [row["dst"] for row in dst_rows if row["dst"] not in skip]
        if not suggestion_sources:
            return []

        # get just the name and source of found suggestions
        placeholders = ",".join("?" * len(suggestion_sources))
        manpage_rows = self._conn.execute(
            f"SELECT name, source FROM parsed_manpages WHERE source IN ({placeholders})",
            suggestion_sources,
        ).fetchall()

        # Apply distro/release filter when requested
        if distro is not None and release is not None:
            prefix = f"{distro}/{release}/"
            manpage_rows = [
                row for row in manpage_rows if row["source"].startswith(prefix)
            ]

        return [
            (row["source"], ParsedManpage(source=row["source"], name=row["name"]))
            for row in manpage_rows
        ]

    def distros(self):
        """Return distinct (distro, release) pairs from manpages."""
        rows = self._conn.execute("""
            SELECT DISTINCT
                SUBSTR(source, 1, INSTR(source, '/') - 1) as distro,
                SUBSTR(source, INSTR(source, '/') + 1,
                       INSTR(SUBSTR(source, INSTR(source, '/') + 1), '/') - 1) as release
            FROM parsed_manpages
        """).fetchall()
        return [(row["distro"], row["release"]) for row in rows]

    def add_mapping(self, src, dst, score):
        self._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)", (src, dst, score)
        )
        self._conn.commit()

    def _upsert_raw_manpage(self, source, raw):
        """Insert or replace a RawManpage into the manpages table."""
        self._conn.execute(
            """INSERT OR REPLACE INTO manpages(source, data, generated_at, generator,
                                               generator_version, source_gz_sha256)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                source,
                _compress(raw.source_text),
                raw.generated_at.isoformat(),
                raw.generator,
                raw.generator_version,
                raw.source_gz_sha256,
            ),
        )

    def add_manpage(self, m, raw):
        """add `m` into the store, if it exists first remove it and its mappings

        each man page may have aliases besides the name determined by its
        basename"""
        validate_source_path(m.source)
        existing = self._conn.execute(
            "SELECT source FROM parsed_manpages WHERE source = ?", (m.source,)
        ).fetchone()
        if existing:
            logger.info("removing old manpage %s", m.source)
            # ON DELETE CASCADE removes all mappings rows for this manpage
            self._conn.execute(
                "DELETE FROM parsed_manpages WHERE source = ?", (m.source,)
            )
            self._conn.commit()
            logger.info("removed manpage and its mappings for %s", m.source)
        else:
            # Check for duplicate: same distro/release + name + section but different source
            distro, release = config.parse_distro_release(m.source)
            section = m.section
            prefix = f"{distro}/{release}/"
            conflict = self._conn.execute(
                "SELECT source FROM parsed_manpages WHERE source LIKE ? AND source != ? AND name = ?",
                (prefix + "%", m.source, m.name),
            ).fetchone()
            if conflict:
                conflict_source = conflict["source"]
                _, conflict_section = util.name_section(
                    os.path.basename(conflict_source)[:-3]
                )
                if conflict_section == section:
                    raise errors.DuplicateManpage(
                        f"manpage {m.name}({section}) already exists with source "
                        f"{conflict_source!r}, refusing to add duplicate from {m.source!r}"
                    )

        self._upsert_raw_manpage(m.source, raw)

        d = m.to_store()
        self._conn.execute(
            """INSERT INTO parsed_manpages(source, name, synopsis, options, aliases,
                                   dashless_opts, has_subcommands, updated, nested_cmd,
                                   extractor, extraction_meta)
               VALUES (:source, :name, :synopsis, :options, :aliases,
                       :dashless_opts, :has_subcommands, :updated, :nested_cmd,
                       :extractor, :extraction_meta)""",
            d,
        )
        self._conn.commit()

        for alias, score in m.aliases:
            self._conn.execute(
                "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
                (alias, m.source, score),
            )
            logger.info(
                "inserting mapping (alias) %s -> %s with score %d",
                alias,
                m.name,
                score,
            )
        self._conn.commit()
        return m

    def names(self):
        for row in self._conn.execute("SELECT source, name FROM parsed_manpages"):
            yield row["source"], row["name"]

    def mappings(self):
        for row in self._conn.execute("SELECT src, dst FROM mappings"):
            yield row["src"], row["dst"]

    def set_has_subcommands(self, source):
        self._conn.execute(
            "UPDATE parsed_manpages SET has_subcommands = 1 WHERE source = ?", (source,)
        )
        self._conn.commit()

    def update_subcommand_mappings(self):
        """Discover sub-command relationships and create mappings.

        For every man page whose name contains a hyphen (e.g. ``git-commit``),
        check whether the prefix (``git``) also exists as a man page.  If so,
        add a mapping ``git commit`` -> ``git-commit`` and mark the parent as
        ``has_subcommands``.
        """
        manpages = {}
        potential = []
        for _id, name in self.names():
            if "-" in name:
                potential.append((name.split("-"), _id))
            else:
                manpages[name] = _id

        existing_mappings = {src for src, _ in self.mappings()}
        mappings_to_add = []
        parents = {}

        for parts, _id in potential:
            joined = " ".join(parts)
            if joined in existing_mappings:
                continue
            if parts[0] in manpages:
                mappings_to_add.append((joined, _id))
                parents[parts[0]] = manpages[parts[0]]

        for src, dst in mappings_to_add:
            self.add_mapping(src, dst, 1)
            logger.info("inserting mapping (subcommand) %s -> %s", src, dst)

        for name, _id in parents.items():
            self.set_has_subcommands(_id)
            logger.info("marking %r as has_subcommands", name)

        return mappings_to_add, parents

    def list_sections(self, distro: str, release: str) -> list[str]:
        """Return distinct section directories for a distro/release.

        Extracts the third path component from source keys matching
        ``distro/release/...`` using a range scan on the primary key.
        """
        prefix = f"{distro}/{release}/"
        offset = len(prefix)
        rows = self._conn.execute(
            "SELECT DISTINCT SUBSTR(source, ?, INSTR(SUBSTR(source, ?), '/') - 1) AS section "
            "FROM manpages WHERE source >= ? AND source < ? "
            "ORDER BY section",
            (offset + 1, offset + 1, prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)),
        ).fetchall()
        return [row["section"] for row in rows]

    def list_manpages(self, prefix: str) -> list[str]:
        """List source paths that start with *prefix*.

        Uses a range scan on the ``manpages`` primary key index.
        """
        rows = self._conn.execute(
            "SELECT source FROM manpages WHERE source >= ? AND source < ?",
            (prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)),
        ).fetchall()
        return [row["source"] for row in rows]

    def get_manpage_source(self, source: str) -> tuple[str, str] | None:
        """Fetch and decompress the raw manpage source text.

        Returns ``(source_text, generator)`` or ``None`` if not stored.
        """
        row = self._conn.execute(
            "SELECT data, generator FROM manpages WHERE source = ?", (source,)
        ).fetchone()
        if row is None:
            return None
        return _decompress(row["data"]), row["generator"]
