#!/usr/bin/env python3
"""
Migrate data from MongoDB JSON exports to the SQLite database.

Usage:
    python tools/migrate_mongo_to_sqlite.py \
        --manpage /tmp/mongo_export/manpage.json \
        --mapping /tmp/mongo_export/mapping.json \
        [--db explainshell.db]
"""

import argparse
import json
import logging
import sqlite3
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS parsed_manpages (
    source        TEXT    PRIMARY KEY,
    name          TEXT    NOT NULL,
    synopsis      TEXT,
    paragraphs    TEXT    NOT NULL DEFAULT '[]',
    aliases       TEXT    NOT NULL DEFAULT '[]',
    dashless_opts INTEGER NOT NULL DEFAULT 0,
    has_subcommands INTEGER NOT NULL DEFAULT 0,
    updated       INTEGER NOT NULL DEFAULT 0,
    nested_cmd    TEXT    NOT NULL DEFAULT 'false'
);

CREATE TABLE IF NOT EXISTS mappings (
    id    INTEGER PRIMARY KEY,
    src   TEXT    NOT NULL,
    dst   TEXT    NOT NULL REFERENCES parsed_manpages(source) ON DELETE CASCADE,
    score INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mappings_src ON mappings(src);
CREATE INDEX IF NOT EXISTS idx_mappings_dst ON mappings(dst);
"""


def _oid(value):
    """Extract string from a MongoDB ObjectId dict like {'$oid': '...'}."""
    if isinstance(value, dict) and "$oid" in value:
        return value["$oid"]
    return str(value)


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return default


def _para_to_store(p):
    """Normalise a paragraph dict so text is a plain string."""
    text = p.get("text", "")
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return {
        "idx": p.get("idx", 0),
        "text": text,
        "section": p.get("section", ""),
        "is_option": p.get("is_option", False),
        # option-specific fields (absent for plain paragraphs)
        **{
            k: p[k]
            for k in ("short", "long", "expectsarg", "argument", "nestedcmd")
            if k in p
        },
    }


def migrate(manpage_file, mapping_file, db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_CREATE_SCHEMA)

    # ------------------------------------------------------------------ #
    # 1. manpages                                                          #
    # ------------------------------------------------------------------ #
    logger.info("Migrating manpages from %s …", manpage_file)
    oid_to_source = {}  # mongo ObjectId string -> source path

    with open(manpage_file) as fh:
        lines = fh.readlines()

    inserted = skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        oid = _oid(doc["_id"])

        synopsis = doc.get("synopsis") or ""
        if isinstance(synopsis, bytes):
            synopsis = synopsis.decode("utf-8")

        paragraphs = [_para_to_store(p) for p in doc.get("paragraphs", [])]
        aliases = doc.get("aliases", [])
        # aliases may be [[name, score], ...] already
        aliases_json = json.dumps(aliases)

        dashless_opts = _coerce_bool(
            doc.get("partial_match", doc.get("partialmatch")), False
        )
        has_subcommands = _coerce_bool(
            doc.get("multi_cmd", doc.get("multicommand")), False
        )
        nested_cmd = doc.get("nested_cmd", doc.get("nestedcmd", False))
        nested_cmd_json = json.dumps(nested_cmd)

        try:
            conn.execute(
                """INSERT INTO parsed_manpages
                       (source, name, synopsis, paragraphs, aliases,
                        dashless_opts, has_subcommands, updated, nested_cmd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["source"],
                    doc["name"],
                    synopsis or None,
                    json.dumps(paragraphs),
                    aliases_json,
                    int(dashless_opts),
                    int(has_subcommands),
                    int(_coerce_bool(doc.get("updated"), False)),
                    nested_cmd_json,
                ),
            )
            oid_to_source[oid] = doc["source"]
            inserted += 1
        except sqlite3.IntegrityError:
            # duplicate source — skip
            skipped += 1

    conn.commit()
    logger.info("manpages: inserted=%d skipped=%d", inserted, skipped)

    # ------------------------------------------------------------------ #
    # 2. mappings                                                          #
    # ------------------------------------------------------------------ #
    logger.info("Migrating mappings from %s …", mapping_file)
    with open(mapping_file) as fh:
        lines = fh.readlines()

    inserted = skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        dst_oid = _oid(doc["dst"])
        new_dst = oid_to_source.get(dst_oid)
        if new_dst is None:
            logger.warning(
                "mapping dst %s not found in manpage table — skipping", dst_oid
            )
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO mappings(src, dst, score) VALUES (?, ?, ?)",
            (doc["src"], new_dst, doc["score"]),
        )
        inserted += 1

    conn.commit()
    logger.info("mappings: inserted=%d skipped=%d", inserted, skipped)

    # ------------------------------------------------------------------ #
    # 3. verify                                                            #
    # ------------------------------------------------------------------ #
    (mp_count,) = conn.execute("SELECT COUNT(*) FROM parsed_manpages").fetchone()
    (map_count,) = conn.execute("SELECT COUNT(*) FROM mappings").fetchone()
    logger.info(
        "Final counts — parsed_manpages: %d, mappings: %d",
        mp_count,
        map_count,
    )
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate MongoDB exports to SQLite")
    parser.add_argument("--manpage", required=True, help="Path to manpage.json export")
    parser.add_argument("--mapping", required=True, help="Path to mapping.json export")
    parser.add_argument(
        "--db",
        default="explainshell.db",
        help="SQLite DB path (default: explainshell.db)",
    )
    args = parser.parse_args()

    ok = migrate(args.manpage, args.mapping, args.db)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
