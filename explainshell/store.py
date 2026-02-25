"""
data objects to save processed man pages to sqlite
"""

import collections
import json
import logging
import re
import sqlite3

from pydantic import BaseModel

from explainshell import errors, help_constants, util, config

logger = logging.getLogger(__name__)

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS manpage (
    id            INTEGER PRIMARY KEY,
    source        TEXT    NOT NULL UNIQUE,  -- basename of the .gz source file
    name          TEXT    NOT NULL,         -- command name (e.g. 'git')
    synopsis      TEXT,                     -- one-line synopsis from the man page
    options       TEXT    NOT NULL DEFAULT '[]',  -- JSON list of option dicts
    aliases       TEXT    NOT NULL DEFAULT '[]',  -- JSON list of [alias, score] pairs
    dashless_opts INTEGER NOT NULL DEFAULT 0,      -- allow matching options without leading '-'
    multi_cmd     INTEGER NOT NULL DEFAULT 0,     -- has sub-commands (e.g. git -> git commit)
    updated       INTEGER NOT NULL DEFAULT 0,     -- manually edited, skip during bulk imports
    nested_cmd    TEXT    NOT NULL DEFAULT 'false' -- positional args start a nested command (e.g. sudo, xargs)
);

-- Maps command names (and aliases) to manpage rows.
-- A single manpage may have many mappings (one per alias).
-- For multi-cmd parents, sub-command mappings are also stored here
-- (e.g. src='git commit' -> dst=<git-commit manpage id>).
CREATE TABLE IF NOT EXISTS mapping (
    id    INTEGER PRIMARY KEY,
    src   TEXT    NOT NULL,      -- lookup key (command name or 'cmd subcmd')
    dst   INTEGER NOT NULL REFERENCES manpage(id) ON DELETE CASCADE,
    score INTEGER NOT NULL       -- higher score = preferred match
);

CREATE INDEX IF NOT EXISTS idx_mapping_src ON mapping(src);
CREATE INDEX IF NOT EXISTS idx_mapping_dst ON mapping(dst);

"""


class Option(BaseModel):
    """An extracted command-line option from a man page.

    short - a list of short options (-a, -b, ..)
    long - a list of long options (--a, --b)
    expects_arg - specifies if one of the short/long options expects an additional argument
    argument - specifies if to consider this as positional arguments
    nested_cmd - specifies if the arguments to this option can start a nested command
    """

    text: str
    short: list[str] = []
    long: list[str] = []
    expects_arg: bool | str | list[str] = False
    argument: str | bool | None = None
    nested_cmd: bool | list[str] = False

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
    multi_cmd - consider sub commands when explaining a command with this man page,
        e.g. git -> git commit
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
    multi_cmd: bool = False
    updated: bool = False
    nested_cmd: bool | str = False

    @property
    def name_section(self):
        name, section = util.name_section(self.source[:-3])
        return f"{name}({section})"

    @property
    def section(self):
        name, section = util.name_section(self.source[:-3])
        return section

    @property
    def arguments(self):
        # go over all options and look for those with the same 'argument' field
        groups = collections.OrderedDict()
        for opt in self.options:
            if opt.argument:
                groups.setdefault(opt.argument, []).append(opt)

        # merge all the options under the same argument to a single string
        for k, ln in groups.items():
            groups[k] = "\n\n".join([p.text for p in ln])

        return groups

    @property
    def synopsis_no_name(self):
        return re.match(r"[\w|-]+ - (.*)$", self.synopsis).group(1)

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
            "multi_cmd": int(bool(self.multi_cmd)),
            "updated": int(bool(self.updated)),
            "nested_cmd": json.dumps(self.nested_cmd),
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
        multi_cmd = bool(d["multi_cmd"])
        nested_cmd = json.loads(d["nested_cmd"])

        return ParsedManpage(
            source=d["source"],
            name=d["name"],
            synopsis=synopsis,
            options=options,
            aliases=[tuple(x) for x in json.loads(d["aliases"])],
            dashless_opts=dashless_opts,
            multi_cmd=multi_cmd,
            updated=bool(d["updated"]),
            nested_cmd=nested_cmd,
        )

    def __repr__(self):
        return f"<manpage {self.name}({self.section}), {len(self.options)} options>"


class Store:
    """read/write processed man pages from sqlite

    we use two tables:
    1) manpage - contains a processed man page
    2) mapping - contains (name, manpageid, score) tuples
    """

    def __init__(self, db_path=config.DB_PATH):
        logger.info("creating store, db_path = %r", db_path)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_CREATE_SCHEMA)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def drop(self, confirm=False):
        if not confirm:
            return

        logger.info("dropping mapping, manpage tables")
        self._conn.executescript("""
            DELETE FROM mapping;
            DELETE FROM manpage;
        """)
        self._conn.commit()

    def __contains__(self, name):
        row = self._conn.execute(
            "SELECT COUNT(*) FROM mapping WHERE src = ?", (name,)
        ).fetchone()
        return row[0] > 0

    def __iter__(self):
        for row in self._conn.execute("SELECT * FROM manpage"):
            yield ParsedManpage.from_store(dict(row))

    def find_man_page(self, name):
        """find a man page by its name, everything following the last dot (.) in name,
        is taken as the section of the man page

        we return the man page found with the highest score, and a list of
        suggestions that also matched the given name (only the first item
        is prepopulated with the option data)"""
        if name.endswith(".gz"):
            logger.info("name ends with .gz, looking up an exact match by source")
            row = self._conn.execute(
                "SELECT * FROM manpage WHERE source = ?", (name,)
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

        logger.info("looking up manpage in mapping with src %r", name)
        mapping_rows = self._conn.execute(
            "SELECT dst, score FROM mapping WHERE src = ?", (name,)
        ).fetchall()

        if not mapping_rows:
            raise errors.ProgramDoesNotExist(name)

        dsts = {row["dst"]: row["score"] for row in mapping_rows}

        placeholders = ",".join("?" * len(dsts))
        manpage_rows = self._conn.execute(
            f"SELECT id, name, source FROM manpage WHERE id IN ({placeholders})",
            list(dsts.keys()),
        ).fetchall()

        if len(manpage_rows) != len(dsts):
            logger.error(
                "one of %r mappings is missing in manpage table "
                "(%d mappings, %d found)",
                dsts,
                len(dsts),
                len(manpage_rows),
            )

        results = [
            (row["id"], ParsedManpage(source=row["source"], name=row["name"]))
            for row in manpage_rows
        ]
        results.sort(key=lambda x: dsts.get(x[0], 0), reverse=True)
        logger.info("got %s", results)

        if section is not None:
            if len(results) > 1:
                results.sort(
                    key=lambda oid_m: oid_m[1].section == section, reverse=True
                )
                logger.info(r"sorting %r so %s is first", results, section)
            if results[0][1].section != section:
                raise errors.ProgramDoesNotExist(orig_name)
            results.extend(self._discover_manpage_suggestions(results[0][0], results))

        oid = results[0][0]
        results = [x[1] for x in results]
        row = self._conn.execute(
            "SELECT * FROM manpage WHERE id = ?", (oid,)
        ).fetchone()
        results[0] = ParsedManpage.from_store(dict(row))
        return results

    def _discover_manpage_suggestions(self, oid, existing):
        """find suggestions for a given man page

        oid is the id of the man page in question,
        existing is a list of (id, man page) of suggestions that were
        already discovered
        """
        skip = {oid for oid, m in existing}

        # find all srcs that point to oid
        src_rows = self._conn.execute(
            "SELECT src FROM mapping WHERE dst = ?", (oid,)
        ).fetchall()
        srcs = [row["src"] for row in src_rows]
        if not srcs:
            return []

        # find all dsts of those srcs
        placeholders = ",".join("?" * len(srcs))
        dst_rows = self._conn.execute(
            f"SELECT DISTINCT dst FROM mapping WHERE src IN ({placeholders})",
            srcs,
        ).fetchall()
        suggestion_ids = [row["dst"] for row in dst_rows if row["dst"] not in skip]
        if not suggestion_ids:
            return []

        # get just the name and source of found suggestions
        placeholders = ",".join("?" * len(suggestion_ids))
        manpage_rows = self._conn.execute(
            f"SELECT id, name, source FROM manpage WHERE id IN ({placeholders})",
            suggestion_ids,
        ).fetchall()
        return [
            (row["id"], ParsedManpage(source=row["source"], name=row["name"]))
            for row in manpage_rows
        ]

    def add_mapping(self, src, dst, score):
        self._conn.execute(
            "INSERT INTO mapping(src, dst, score) VALUES (?, ?, ?)", (src, dst, score)
        )
        self._conn.commit()

    def add_manpage(self, m):
        """add `m` into the store, if it exists first remove it and its mappings

        each man page may have aliases besides the name determined by its
        basename"""
        existing = self._conn.execute(
            "SELECT id FROM manpage WHERE source = ?", (m.source,)
        ).fetchone()
        if existing:
            old_id = existing["id"]
            logger.info("removing old manpage %s (%s)", m.source, old_id)
            # ON DELETE CASCADE removes all mapping rows for this manpage
            self._conn.execute("DELETE FROM manpage WHERE id = ?", (old_id,))
            self._conn.commit()
            logger.info("removed manpage and its mappings for %s", m.source)

        d = m.to_store()
        cursor = self._conn.execute(
            """INSERT INTO manpage(source, name, synopsis, options, aliases,
                                   dashless_opts, multi_cmd, updated, nested_cmd)
               VALUES (:source, :name, :synopsis, :options, :aliases,
                       :dashless_opts, :multi_cmd, :updated, :nested_cmd)""",
            d,
        )
        self._conn.commit()
        new_id = cursor.lastrowid

        for alias, score in m.aliases:
            self._conn.execute(
                "INSERT INTO mapping(src, dst, score) VALUES (?, ?, ?)",
                (alias, new_id, score),
            )
            logger.info(
                "inserting mapping (alias) %s -> %s (%s) with score %d",
                alias,
                m.name,
                new_id,
                score,
            )
        self._conn.commit()
        return m

    def update_man_page(self, m):
        """update m and add new aliases if necessary

        change updated attribute so we don't overwrite this in the future"""
        logger.info("updating manpage %s", m.source)
        m.updated = True
        d = m.to_store()
        self._conn.execute(
            """UPDATE manpage
               SET name=:name, synopsis=:synopsis, options=:options,
                   aliases=:aliases, dashless_opts=:dashless_opts,
                   multi_cmd=:multi_cmd, updated=:updated, nested_cmd=:nested_cmd
               WHERE source=:source""",
            d,
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT id FROM manpage WHERE source = ?", (m.source,)
        ).fetchone()
        manpage_id = row["id"]

        for alias, score in m.aliases:
            if alias not in self:
                self._conn.execute(
                    "INSERT INTO mapping(src, dst, score) VALUES (?, ?, ?)",
                    (alias, manpage_id, score),
                )
                self._conn.commit()
                logger.info(
                    "inserting mapping (alias) %s -> %s (%s) with score %d",
                    alias,
                    m.name,
                    manpage_id,
                    score,
                )
            else:
                logger.debug(
                    "mapping (alias) %s -> %s (%s) already exists",
                    alias,
                    m.name,
                    manpage_id,
                )
        return m

    def verify(self):
        # check that everything in manpage is reachable
        mapping_rows = self._conn.execute("SELECT dst FROM mapping").fetchall()
        reachable = {row["dst"] for row in mapping_rows}

        manpage_rows = self._conn.execute("SELECT id FROM manpage").fetchall()
        man_pages = {row["id"] for row in manpage_rows}

        ok = True
        unreachable = man_pages - reachable
        if unreachable:
            logger.error(
                "manpages %r are unreachable (nothing maps to them)", unreachable
            )
            placeholders = ",".join("?" * len(unreachable))
            name_rows = self._conn.execute(
                f"SELECT name FROM manpage WHERE id IN ({placeholders})",
                list(unreachable),
            ).fetchall()
            unreachable = [row["name"] for row in name_rows]
            ok = False

        notfound = reachable - man_pages
        if notfound:
            logger.error("mappings to non-existing manpages: %r", notfound)
            ok = False

        return ok, unreachable, notfound

    def names(self):
        for row in self._conn.execute("SELECT id, name FROM manpage"):
            yield row["id"], row["name"]

    def mappings(self):
        for row in self._conn.execute("SELECT src, id FROM mapping"):
            yield row["src"], row["id"]

    def set_multi_cmd(self, manpage_id):
        self._conn.execute(
            "UPDATE manpage SET multi_cmd = 1 WHERE id = ?", (manpage_id,)
        )
        self._conn.commit()

    def update_multi_cmd_mappings(self):
        """Discover sub-command relationships and create mappings.

        For every man page whose name contains a hyphen (e.g. ``git-commit``),
        check whether the prefix (``git``) also exists as a man page.  If so,
        add a mapping ``git commit`` -> ``git-commit`` and mark the parent as
        ``multi_cmd``.
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
        multi_cmds = {}

        for parts, _id in potential:
            joined = " ".join(parts)
            if joined in existing_mappings:
                continue
            if parts[0] in manpages:
                mappings_to_add.append((joined, _id))
                multi_cmds[parts[0]] = manpages[parts[0]]

        for src, dst in mappings_to_add:
            self.add_mapping(src, dst, 1)
            logger.info("inserting mapping (multi_cmd) %s -> %s", src, dst)

        for name, _id in multi_cmds.items():
            self.set_multi_cmd(_id)
            logger.info("making %r a multi_cmd", name)

        return mappings_to_add, multi_cmds
