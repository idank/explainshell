"""
data objects to save processed man pages to sqlite
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import zlib
from collections.abc import Iterator
from typing import NamedTuple

from explainshell import errors, util, config
from explainshell.models import ParsedManpage, RawManpage

logger = logging.getLogger(__name__)

_SOURCE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*/[^/]+/[^/]+/[^/]+\.\d\w*\.gz$")


def validate_source_path(source: str) -> None:
    """Validate that *source* has the ``distro/release/section/name.section.gz`` format.

    Raises ``errors.InvalidSourcePath`` on failure.
    """
    if not _SOURCE_RE.match(source):
        raise errors.InvalidSourcePath(
            f"source path {source!r} does not match the required "
            f"'distro/release/section/name.section.gz' format "
            f"(e.g. 'ubuntu/25.10/1/tar.1.gz')"
        )


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
    subcommands   TEXT    NOT NULL DEFAULT '[]',  -- JSON list of subcommand names (e.g. ["build","run","push"])
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
    src   TEXT    NOT NULL,      -- lookup key (command name or 'cmd subcmd')
    dst   TEXT    NOT NULL REFERENCES parsed_manpages(source) ON DELETE CASCADE,
    score INTEGER NOT NULL,      -- higher score = preferred match
    PRIMARY KEY (src, dst)
);

CREATE INDEX IF NOT EXISTS idx_mappings_dst ON mappings(dst);
CREATE INDEX IF NOT EXISTS idx_mappings_src ON mappings(src, dst, score);

"""


def _compress(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"))


def _decompress(data: bytes) -> str:
    return zlib.decompress(data).decode("utf-8")


def _dr_prefix(source: str) -> str:
    """Extract 'distro/release/' prefix from a source path."""
    parts = source.split("/", 2)
    return f"{parts[0]}/{parts[1]}/"


class SubcommandMappingResult(NamedTuple):
    """Result of update_subcommand_mappings_llm / _heuristic."""

    mappings_added: list[tuple[str, str]]  # (src, dst) pairs added
    parents: dict[str, str]  # parent name -> source


class Store:
    """read/write processed man pages from sqlite"""

    def __init__(self, db_path: str, read_only: bool = False) -> None:
        logger.info("creating store, db_path = %r, read_only = %s", db_path, read_only)
        # check_same_thread=False: the default sqlite3 driver raises if a
        # connection is used from a thread other than the one that created it.
        # Each Store instance is used by a single thread (the web layer
        # creates a per-request Store), so this is safe.
        if read_only:
            self._conn = sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def create(cls, db_path: str) -> "Store":
        """Create a new (or open an existing) writable database and return a Store."""
        s = cls(db_path)
        s._conn.executescript(_CREATE_SCHEMA)
        return s

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def drop(self, confirm: bool = False) -> None:
        if not confirm:
            return

        logger.info("dropping mappings, parsed_manpages, manpages tables")
        self._conn.executescript("""
            DELETE FROM mappings;
            DELETE FROM parsed_manpages;
            DELETE FROM manpages;
        """)
        self._conn.commit()

    def find_man_page(
        self, name: str, distro: str | None = None, release: str | None = None
    ) -> list[ParsedManpage]:
        """find a man page by its name, everything following the last dot (.) in name,
        is taken as the section of the man page

        we return the man page found with the highest score, and a list of
        suggestions that also matched the given name (only the first item
        is prepopulated with the option data)

        when distro and release are set, filter results to manpages whose
        source starts with ``distro/release/``."""
        if name.endswith(".gz"):
            logger.debug("name ends with .gz, looking up an exact match by source")
            row = self._conn.execute(
                "SELECT * FROM parsed_manpages WHERE source = ?", (name,)
            ).fetchone()
            if not row:
                raise errors.ProgramDoesNotExist(name)
            m = ParsedManpage.from_store(dict(row))
            logger.debug("returning %s", m)
            return [m]

        section = None
        orig_name = name

        # don't try to look for a section if it's . (source)
        if name != ".":
            splitted = name.rsplit(".", 1)
            name = splitted[0]
            if len(splitted) > 1:
                section = splitted[1]

        logger.debug("looking up manpage in mappings with src %r", name)
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
        logger.debug(
            "found %d candidates: %s",
            len(results),
            [(src, m.name_section) for src, m in results],
        )

        if section is not None:
            if len(results) > 1:
                results.sort(
                    key=lambda src_m: src_m[1].section == section, reverse=True
                )
                logger.debug("sorted candidates so section %s is first", section)
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

    def has_manpage_source(self, source: str) -> bool:
        """Return whether *source* exists in parsed_manpages."""
        row = self._conn.execute(
            "SELECT 1 FROM parsed_manpages WHERE source = ? LIMIT 1", (source,)
        ).fetchone()
        return row is not None

    def delete_manpage(self, source: str) -> bool:
        """Delete a manpage and its mappings (via CASCADE) from the store.

        Deleting from manpages cascades to parsed_manpages, which in turn
        cascades to mappings.

        Returns True if a row was deleted, False if the source was not found.
        """
        cur = self._conn.execute("DELETE FROM manpages WHERE source = ?", (source,))
        if cur.rowcount:
            self._conn.commit()
            return True
        return False

    def known_sha256s(self) -> dict[str, str]:
        """Return a mapping of source_gz_sha256 → source for all stored manpages.

        Only includes rows that have both a sha256 and a matching parsed_manpages
        entry.  When multiple sources share the same hash, an arbitrary one wins.
        """
        rows = self._conn.execute(
            "SELECT m.source_gz_sha256, m.source FROM manpages m"
            " JOIN parsed_manpages p ON p.source = m.source"
            " WHERE m.source_gz_sha256 IS NOT NULL"
        ).fetchall()
        return {row["source_gz_sha256"]: row["source"] for row in rows}

    def counts(self) -> dict[str, int]:
        """Return row counts for core tables."""
        return {
            "manpages": self._conn.execute(
                "SELECT COUNT(*) FROM parsed_manpages"
            ).fetchone()[0],
            "mappings": self._conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[
                0
            ],
        }

    def _discover_manpage_suggestions(
        self,
        source: str,
        existing: list[tuple[str, ParsedManpage]],
        distro: str | None = None,
        release: str | None = None,
    ) -> list[tuple[str, ParsedManpage]]:
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

    def distros(self) -> list[tuple[str, str]]:
        """Return distinct (distro, release) pairs from manpages."""
        rows = self._conn.execute("""
            SELECT DISTINCT
                SUBSTR(source, 1, INSTR(source, '/') - 1) as distro,
                SUBSTR(source, INSTR(source, '/') + 1,
                       INSTR(SUBSTR(source, INSTR(source, '/') + 1), '/') - 1) as release
            FROM parsed_manpages
        """).fetchall()
        return [(row["distro"], row["release"]) for row in rows]

    def distros_for_name(self, name: str) -> list[tuple[str, str]]:
        """Return (distro, release) pairs that have a manpage matching *name*."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT
                SUBSTR(pm.source, 1, INSTR(pm.source, '/') - 1) as distro,
                SUBSTR(pm.source, INSTR(pm.source, '/') + 1,
                       INSTR(SUBSTR(pm.source, INSTR(pm.source, '/') + 1), '/') - 1) as release
            FROM mappings m
            JOIN parsed_manpages pm ON pm.source = m.dst
            WHERE m.src = ?
            """,
            (name,),
        ).fetchall()
        return [(row["distro"], row["release"]) for row in rows]

    def add_mapping(self, src: str, dst: str, score: int) -> None:
        self._conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)", (src, dst, score)
        )
        self._conn.commit()

    def has_mapping(self, src: str, dst: str) -> bool:
        """Return whether a mapping from *src* to *dst* already exists."""
        row = self._conn.execute(
            "SELECT 1 FROM mappings WHERE src = ? AND dst = ? LIMIT 1", (src, dst)
        ).fetchone()
        return row is not None

    def mapping_score(self, src: str, dst: str) -> int | None:
        """Return the score of the mapping from *src* to *dst*, or None."""
        row = self._conn.execute(
            "SELECT score FROM mappings WHERE src = ? AND dst = ? LIMIT 1", (src, dst)
        ).fetchone()
        return row["score"] if row else None

    def update_mapping_score(self, src: str, dst: str, score: int) -> None:
        """Update the score of an existing mapping."""
        self._conn.execute(
            "UPDATE mappings SET score = ? WHERE src = ? AND dst = ?",
            (score, src, dst),
        )
        self._conn.commit()

    def _upsert_raw_manpage(self, source: str, raw: RawManpage) -> None:
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

    def add_manpage(self, m: ParsedManpage, raw: RawManpage) -> ParsedManpage:
        """add `m` into the store, if it exists first remove it and its mappings

        each man page may have aliases besides the name determined by its
        basename"""
        validate_source_path(m.source)
        existing = self._conn.execute(
            "SELECT source FROM parsed_manpages WHERE source = ?", (m.source,)
        ).fetchone()
        if existing:
            logger.debug("removing old manpage %s", m.source)
            # Warn about non-alias mappings (e.g. symlink-derived) that will
            # be lost to the CASCADE delete.  Re-running extraction with the
            # symlink paths in the input will recreate them.
            alias_srcs = {a for a, _ in m.aliases}
            lost = [
                row["src"]
                for row in self._conn.execute(
                    "SELECT src FROM mappings WHERE dst = ?", (m.source,)
                ).fetchall()
                if row["src"] not in alias_srcs
            ]
            if lost:
                logger.warning(
                    "re-importing %s will drop non-alias mappings: %s "
                    "(rerun extraction with the original paths to restore them)",
                    m.source,
                    ", ".join(sorted(lost)),
                )
            # ON DELETE CASCADE removes all mappings rows for this manpage.
            self._conn.execute(
                "DELETE FROM parsed_manpages WHERE source = ?", (m.source,)
            )
            self._conn.commit()
            logger.debug("removed manpage and its mappings for %s", m.source)
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
                                   dashless_opts, subcommands, updated, nested_cmd,
                                   extractor, extraction_meta)
               VALUES (:source, :name, :synopsis, :options, :aliases,
                       :dashless_opts, :subcommands, :updated, :nested_cmd,
                       :extractor, :extraction_meta)""",
            d,
        )
        self._conn.commit()

        for alias, score in m.aliases:
            self._conn.execute(
                "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
                (alias, m.source, score),
            )
            logger.debug(
                "inserting mapping (alias) %s -> %s with score %d",
                alias,
                m.name,
                score,
            )

        self._conn.commit()
        return m

    def names(self) -> Iterator[tuple[str, str]]:
        for row in self._conn.execute("SELECT source, name FROM parsed_manpages"):
            yield row["source"], row["name"]

    def mappings(self) -> Iterator[tuple[str, str]]:
        for row in self._conn.execute("SELECT src, dst FROM mappings"):
            yield row["src"], row["dst"]

    def _set_subcommands(self, source: str, subcommands: list[str]) -> None:
        self._conn.execute(
            "UPDATE parsed_manpages SET subcommands = ? WHERE source = ?",
            (json.dumps(subcommands), source),
        )
        self._conn.commit()

    def update_subcommand_mappings_llm(self) -> SubcommandMappingResult:
        """Reconcile subcommand mappings using declared subcommands from LLM-extracted pages.

        For each parent manpage that has a non-empty ``subcommands`` list,
        find matching child manpages (hyphenated names like ``git-commit``)
        in the same distro/release and create ``"git commit"`` → child mappings.

        This is a full reconciliation: all existing subcommand mappings
        (``src LIKE '% %'``) are deleted first, then the correct set is
        re-inserted.  This cleans up stale mappings left by prior runs
        (e.g. a parent whose subcommands list shrank after re-extraction).
        """
        hyphenated: dict[str, list[str]] = {}  # full hyphenated name -> [source, ...]
        # name -> [(source, subcommands), ...] — only non-empty subcommands
        llm_parents: dict[str, list[tuple[str, list[str]]]] = {}

        rows = self._conn.execute(
            "SELECT source, name, subcommands FROM parsed_manpages"
        ).fetchall()
        for row in rows:
            name = row["name"]
            source = row["source"]
            subcommands = json.loads(row["subcommands"])

            if subcommands:
                llm_parents.setdefault(name, []).append((source, subcommands))
            if "-" in name:
                hyphenated.setdefault(name, []).append(source)

        # Compute the full set of valid subcommand mappings.
        valid_mappings: set[tuple[str, str]] = set()
        parents: dict[str, str] = {}

        for parent_name, parent_entries in llm_parents.items():
            for parent_source, subcommands in parent_entries:
                prefix = _dr_prefix(parent_source)
                for sub in subcommands:
                    child_name = f"{parent_name}-{sub}"
                    for child_source in hyphenated.get(child_name, []):
                        if not child_source.startswith(prefix):
                            continue
                        joined = f"{parent_name} {sub}"
                        valid_mappings.add((joined, child_source))
                        parents[parent_name] = parent_source

        # Delete all existing subcommand mappings and re-insert the valid set
        # in a single transaction.  Exclude alias mappings where src matches
        # the manpage name (e.g. "pg_autoctl activate" whose name has a space).
        deleted = self._conn.execute(
            "DELETE FROM mappings WHERE src LIKE '% %' "
            "AND src NOT IN (SELECT name FROM parsed_manpages WHERE name LIKE '% %')"
        ).rowcount
        if deleted:
            logger.debug("deleted %d existing subcommand mapping(s)", deleted)

        mappings_to_add = sorted(valid_mappings)
        self._conn.executemany(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, 1)",
            [(src, dst) for src, dst in mappings_to_add],
        )
        self._conn.commit()

        return SubcommandMappingResult(mappings_to_add, parents)

    def update_subcommand_mappings_heuristic(self) -> SubcommandMappingResult:
        """Create subcommand mappings using a naming-convention heuristic.

        If a manpage name contains a hyphen (e.g. ``git-commit``) and the
        prefix (``git``) exists as another manpage in the same distro/release,
        create a ``"git commit"`` → child mapping.  Also sets the
        ``subcommands`` list on the parent.
        """
        manpages: dict[str, list[str]] = {}  # name -> [source, ...]
        hyphenated: dict[str, list[str]] = {}  # full hyphenated name -> [source, ...]

        rows = self._conn.execute("SELECT source, name FROM parsed_manpages").fetchall()
        for row in rows:
            name = row["name"]
            source = row["source"]
            if "-" in name:
                hyphenated.setdefault(name, []).append(source)
            else:
                manpages.setdefault(name, []).append(source)

        existing_mappings = {(src, dst) for src, dst in self.mappings()}
        new_mappings: set[tuple[str, str]] = set()
        parents: dict[str, str] = {}

        for name, sources in hyphenated.items():
            for source in sources:
                joined = name.replace("-", " ")
                parent_name = name.split("-", 1)[0]
                if parent_name not in manpages:
                    continue
                prefix = _dr_prefix(source)
                if not any(p.startswith(prefix) for p in manpages[parent_name]):
                    continue
                if (joined, source) in existing_mappings:
                    continue
                new_mappings.add((joined, source))
                parents[parent_name] = next(
                    p for p in manpages[parent_name] if p.startswith(prefix)
                )

        mappings_to_add = sorted(new_mappings)
        for src, dst in mappings_to_add:
            self.add_mapping(src, dst, 1)
            logger.debug("inserting mapping (subcommand) %s -> %s", src, dst)

        # Set subcommands on parents that don't already have them.
        for name, source in parents.items():
            prefix = name + "-"
            children = [
                child_name[len(prefix) :]
                for child_name in hyphenated
                if child_name.startswith(prefix)
            ]
            if children:
                self._set_subcommands(source, children)
                logger.debug("setting subcommands on %r: %s", name, children)

        return SubcommandMappingResult(mappings_to_add, parents)

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

    def get_raw_manpage(self, source: str) -> RawManpage | None:
        """Fetch and decompress a raw manpage.

        Returns a ``RawManpage`` or ``None`` if not stored.
        """
        row = self._conn.execute(
            "SELECT data, generated_at, generator, generator_version, source_gz_sha256 "
            "FROM manpages WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return None
        return RawManpage(
            source_text=_decompress(row["data"]),
            generated_at=datetime.datetime.fromisoformat(row["generated_at"]),
            generator=row["generator"],
            generator_version=row["generator_version"],
            source_gz_sha256=row["source_gz_sha256"],
        )
