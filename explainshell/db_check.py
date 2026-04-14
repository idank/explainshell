"""Database integrity checks for explainshell.

Checks:
    - Malformed source paths (must be distro/release/section/name.section.gz)
    - Shadowed duplicates (same name+section+distro from different sources)
    - Orphaned mappings (mapping rows referencing non-existent manpage sources)
    - Unreachable manpages (manpages with no mapping pointing to them)
    - positional set on flagged options (positional should only be on positional operands)
    - Stale subcommand mappings (mapping for "cmd sub" but parent doesn't declare it)
"""

import json
import os
import sqlite3

from explainshell import config, errors, util
from explainshell.store import validate_source_path


def check(db_path: str) -> list[tuple[str, str]]:
    """Run integrity checks and return a list of (severity, message) tuples."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    issues: list[tuple[str, str]] = []

    # 1. Malformed source paths.
    for row in conn.execute("SELECT source, name FROM parsed_manpages"):
        try:
            validate_source_path(row["source"])
        except errors.InvalidSourcePath:
            issues.append(
                (
                    "error",
                    f"malformed source path: {row['source']!r} "
                    f"(manpage {row['name']!r})",
                )
            )

    # 2. Shadowed duplicates: same name+section+distro from different sources.
    rows = conn.execute("SELECT source, name FROM parsed_manpages").fetchall()
    seen: dict[tuple[str, str, str, str], str] = {}
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
            issues.append(
                (
                    "error",
                    f"shadowed duplicate: {name}({section}) in {distro}/{release} "
                    f"from both {seen[key]!r} and {source!r}",
                )
            )
        else:
            seen[key] = source

    # 3. Orphaned mappings: mapping rows referencing non-existent manpage sources.
    orphans = conn.execute(
        "SELECT m.src, m.dst FROM mappings m "
        "LEFT JOIN parsed_manpages mp ON m.dst = mp.source WHERE mp.source IS NULL"
    ).fetchall()
    for row in orphans:
        issues.append(
            (
                "error",
                f"orphaned mapping: src={row['src']!r} -> dst={row['dst']!r} "
                f"(manpage does not exist)",
            )
        )

    # 4. positional set on flagged options.
    for row in conn.execute("SELECT source, name, options FROM parsed_manpages"):
        opts_json = row["options"]
        if not opts_json:
            continue
        try:
            opts = json.loads(opts_json)
        except (json.JSONDecodeError, TypeError) as exc:
            issues.append(
                (
                    "error",
                    f"corrupt options JSON: {row['name']!r} ({row['source']!r}): {exc}",
                )
            )
            continue
        for o in opts:
            short = o.get("short") or []
            long = o.get("long") or []
            positional = o.get("positional")
            if positional and (short or long):
                flags = short + long
                issues.append(
                    (
                        "warning",
                        f"positional on flagged option: {row['name']!r} has "
                        f"positional={positional!r} on option {flags}",
                    )
                )

    # 5. Stale subcommand mappings: subcommand mapping exists but the parent
    #    manpage doesn't declare that subcommand.
    # Exclude alias mappings where src matches the manpage name (e.g.
    # "pg_autoctl config check" is a real manpage name, not a subcommand).
    subcmd_mappings = conn.execute(
        "SELECT m.src, m.dst FROM mappings m "
        "JOIN parsed_manpages mp ON m.dst = mp.source "
        "WHERE m.src LIKE '% %' AND m.src != mp.name"
    ).fetchall()
    for row in subcmd_mappings:
        src = row["src"]
        dst = row["dst"]
        parent_name = src.split(" ", 1)[0]
        sub_name = src.split(" ", 1)[1]
        # Scope parent lookup to the same distro/release as the mapping dst.
        # dst format: "distro/release/section/file.gz"
        dr_prefix = dst.rsplit("/", 2)[0] + "/"  # "distro/release/"
        parent_row = conn.execute(
            "SELECT subcommands FROM parsed_manpages "
            "WHERE name = ? AND source LIKE ? LIMIT 1",
            (parent_name, dr_prefix + "%"),
        ).fetchone()
        if parent_row is None:
            issues.append(
                (
                    "error",
                    f"stale subcommand mapping: {src!r} -> {dst!r} "
                    f"(parent {parent_name!r} does not exist)",
                )
            )
        else:
            subcommands = json.loads(parent_row["subcommands"])
            if sub_name not in subcommands:
                issues.append(
                    (
                        "warning",
                        f"stale subcommand mapping: {src!r} -> {dst!r} "
                        f"(parent {parent_name!r} does not declare "
                        f"{sub_name!r} in subcommands)",
                    )
                )

    # 6. Unreachable manpages: manpages with no mapping pointing to them.
    unreachable = conn.execute(
        "SELECT mp.name, mp.source FROM parsed_manpages mp "
        "LEFT JOIN mappings m ON mp.source = m.dst WHERE m.src IS NULL"
    ).fetchall()
    for row in unreachable:
        issues.append(
            (
                "warning",
                f"unreachable manpage: {row['name']!r} ({row['source']!r}) "
                f"has no mappings",
            )
        )

    conn.close()
    return issues
