"""Check a manpage database for integrity issues.

Usage:
    python tools/db_check.py [--db PATH]

Checks:
    - Malformed source paths (must be distro/release/section/name.section.gz)
    - Shadowed duplicates (same name+section+distro from different sources)
    - Orphaned mappings (mapping rows referencing non-existent manpage IDs)
    - Unreachable manpages (manpages with no mapping pointing to them)
    - positional set on flagged options (positional should only be on positional operands)
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from explainshell import config, errors, util
from explainshell.store import validate_source_path

_RED = "\033[31m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def check(db_path):
    """Run integrity checks and return a list of (severity, message) tuples."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    issues = []

    # 1. Malformed source paths.
    for row in conn.execute("SELECT id, source, name FROM manpage"):
        try:
            validate_source_path(row["source"])
        except errors.InvalidSourcePath:
            issues.append((
                "error",
                f"malformed source path: {row['source']!r} "
                f"(manpage {row['name']!r}, id={row['id']})",
            ))

    # 2. Shadowed duplicates: same name+section+distro from different sources.
    rows = conn.execute("SELECT id, source, name FROM manpage").fetchall()
    seen = {}  # (name, section, distro, release) -> source
    for row in rows:
        source = row["source"]
        name = row["name"]
        try:
            distro, release = config.parse_distro_release(source)
        except (IndexError, ValueError):
            continue  # already caught by malformed-source check
        _, section = util.name_section(os.path.basename(source)[:-3])
        key = (name, section, distro, release)
        if key in seen:
            issues.append((
                "error",
                f"shadowed duplicate: {name}({section}) in {distro}/{release} "
                f"from both {seen[key]!r} and {source!r}",
            ))
        else:
            seen[key] = source

    # 3. Orphaned mappings: mapping rows referencing non-existent manpage IDs.
    orphans = conn.execute(
        "SELECT m.id, m.src, m.dst FROM mapping m "
        "LEFT JOIN manpage mp ON m.dst = mp.id WHERE mp.id IS NULL"
    ).fetchall()
    for row in orphans:
        issues.append((
            "error",
            f"orphaned mapping: src={row['src']!r} -> dst={row['dst']} "
            f"(manpage does not exist)",
        ))

    # 4. positional set on flagged options.
    for row in conn.execute("SELECT id, source, name, options FROM manpage"):
        opts_json = row["options"]
        if not opts_json:
            continue
        try:
            opts = json.loads(opts_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for o in opts:
            short = o.get("short") or []
            long = o.get("long") or []
            positional = o.get("positional")
            if positional and (short or long):
                flags = short + long
                issues.append((
                    "warning",
                    f"positional on flagged option: {row['name']!r} has "
                    f"positional={positional!r} on option {flags}",
                ))

    # 5. Unreachable manpages: manpages with no mapping pointing to them.
    unreachable = conn.execute(
        "SELECT mp.id, mp.name, mp.source FROM manpage mp "
        "LEFT JOIN mapping m ON mp.id = m.dst WHERE m.id IS NULL"
    ).fetchall()
    for row in unreachable:
        issues.append((
            "warning",
            f"unreachable manpage: {row['name']!r} ({row['source']!r}, "
            f"id={row['id']}) has no mappings",
        ))

    conn.close()
    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Check a manpage database for integrity issues."
    )
    parser.add_argument("--db", default=config.DB_PATH, help="SQLite DB path")
    args = parser.parse_args()

    issues = check(args.db)
    if not issues:
        print("No issues found.")
        return 0

    errors_count = sum(1 for sev, _ in issues if sev == "error")
    warnings_count = sum(1 for sev, _ in issues if sev == "warning")
    for severity, msg in issues:
        label = f"{_RED}ERROR{_RESET}" if severity == "error" else f"{_CYAN}WARNING{_RESET}"
        print(f"  {label}: {msg}")
    print(f"\n{errors_count} error(s), {warnings_count} warning(s)")
    return 1 if errors_count else 0


if __name__ == "__main__":
    sys.exit(main())
