#!/usr/bin/env python3
"""Benchmark the impact of the idx_mappings_src index on query performance.

Measures the three hot-path queries that filter on mappings.src (the queries
hit by every /explain?cmd= request) with and without the index.

Usage:
    python tools/bench_src_index.py <db_path>
    python tools/bench_src_index.py <db_path> --iterations 5000
"""

import argparse
import random
import sqlite3
import statistics
import time


# ---------------------------------------------------------------------------
# The three query shapes exercised by /explain?cmd= that benefit from the
# idx_mappings_src index:
#
# 1. find_man_page  (store.py:169-170)
#    SELECT dst, score FROM mappings WHERE src = ?
#
# 2. distros_for_name  (store.py:350-361)
#    SELECT DISTINCT ... FROM mappings m JOIN parsed_manpages pm
#    ON pm.source = m.dst WHERE m.src = ?
#
# 3. _discover_manpage_suggestions  (store.py:310-312)
#    SELECT DISTINCT dst FROM mappings WHERE src IN (?, ?, ...)
# ---------------------------------------------------------------------------

QUERY_FIND = "SELECT dst, score FROM mappings WHERE src = ?"

QUERY_DISTROS = """\
SELECT DISTINCT
    SUBSTR(pm.source, 1, INSTR(pm.source, '/') - 1) as distro,
    SUBSTR(pm.source, INSTR(pm.source, '/') + 1,
           INSTR(SUBSTR(pm.source, INSTR(pm.source, '/') + 1), '/') - 1) as release
FROM mappings m
JOIN parsed_manpages pm ON pm.source = m.dst
WHERE m.src = ?
"""


def build_query_in(n: int) -> str:
    placeholders = ",".join("?" * n)
    return f"SELECT DISTINCT dst FROM mappings WHERE src IN ({placeholders})"


def collect_sample_srcs(
    conn: sqlite3.Connection, n: int
) -> tuple[list[str], list[str], list[str]]:
    """Return three lists of src values for benchmarking.

    - single_dst: src values that map to exactly 1 destination (common case)
    - multi_dst:  src values that map to >1 destination
    - subcommand: src values containing a space (e.g. 'git commit')
    """
    single = [
        r[0]
        for r in conn.execute(
            "SELECT src FROM mappings GROUP BY src HAVING COUNT(*) = 1 "
            "ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
    ]
    multi = [
        r[0]
        for r in conn.execute(
            "SELECT src FROM mappings GROUP BY src HAVING COUNT(*) > 1 "
            "ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
    ]
    subcmd = [
        r[0]
        for r in conn.execute(
            "SELECT src FROM mappings WHERE src LIKE '% %' ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
    ]
    return single, multi, subcmd


def bench_query(
    conn: sqlite3.Connection,
    query: str,
    params_list: list[tuple],
    iterations: int,
) -> list[float]:
    """Run *query* with each params tuple, cycling through params_list for
    *iterations* total executions. Return per-query times in seconds."""
    times: list[float] = []
    for i in range(iterations):
        params = params_list[i % len(params_list)]
        start = time.perf_counter()
        conn.execute(query, params).fetchall()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return times


def bench_in_query(
    conn: sqlite3.Connection,
    src_groups: list[list[str]],
    iterations: int,
) -> list[float]:
    """Benchmark the IN (...) query used by _discover_manpage_suggestions."""
    times: list[float] = []
    for i in range(iterations):
        group = src_groups[i % len(src_groups)]
        query = build_query_in(len(group))
        start = time.perf_counter()
        conn.execute(query, group).fetchall()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return times


def report(label: str, times: list[float]) -> dict[str, float]:
    """Print and return summary stats for a list of timings."""
    us = [t * 1e6 for t in times]
    stats = {
        "mean_us": statistics.mean(us),
        "median_us": statistics.median(us),
        "p95_us": sorted(us)[int(len(us) * 0.95)],
        "p99_us": sorted(us)[int(len(us) * 0.99)],
        "min_us": min(us),
        "max_us": max(us),
    }
    print(f"  {label}:")
    print(
        f"    mean={stats['mean_us']:.1f}us  "
        f"median={stats['median_us']:.1f}us  "
        f"p95={stats['p95_us']:.1f}us  "
        f"p99={stats['p99_us']:.1f}us  "
        f"min={stats['min_us']:.1f}us  "
        f"max={stats['max_us']:.1f}us"
    )
    return stats


def run_suite(
    conn: sqlite3.Connection,
    label: str,
    srcs_single: list[str],
    srcs_multi: list[str],
    srcs_subcmd: list[str],
    iterations: int,
) -> dict[str, dict[str, float]]:
    """Run all benchmark queries and return stats dict."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")

    results: dict[str, dict[str, float]] = {}

    # Warm up the page cache
    conn.execute("SELECT COUNT(*) FROM mappings").fetchone()
    conn.execute("SELECT COUNT(*) FROM parsed_manpages").fetchone()

    # 1) find_man_page — single destination
    params = [(s,) for s in srcs_single]
    times = bench_query(conn, QUERY_FIND, params, iterations)
    results["find_man_page (1 dst)"] = report("find_man_page (1 dst)", times)

    # 2) find_man_page — multiple destinations
    params = [(s,) for s in srcs_multi]
    times = bench_query(conn, QUERY_FIND, params, iterations)
    results["find_man_page (N dst)"] = report("find_man_page (N dst)", times)

    # 3) distros_for_name
    params = [(s,) for s in srcs_single + srcs_multi]
    random.shuffle(params)
    times = bench_query(conn, QUERY_DISTROS, params, iterations)
    results["distros_for_name"] = report("distros_for_name", times)

    # 4) IN query (subcommand suggestions)
    # Build groups of 2-5 src values to simulate real usage
    src_pool = srcs_single + srcs_multi + srcs_subcmd
    groups = []
    for _ in range(max(iterations, 200)):
        k = random.randint(2, min(5, len(src_pool)))
        groups.append(random.sample(src_pool, k))
    times = bench_in_query(conn, groups, iterations)
    results["suggestions IN(...)"] = report("suggestions IN(...)", times)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark impact of idx_mappings_src index"
    )
    parser.add_argument("db_path", help="Path to the explainshell SQLite database")
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=2000,
        help="Number of query iterations per benchmark (default: 2000)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    # Match production settings
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache

    total_mappings = conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
    total_manpages = conn.execute("SELECT COUNT(*) FROM parsed_manpages").fetchone()[0]
    print(f"Database: {args.db_path}")
    print(f"  mappings:        {total_mappings:,}")
    print(f"  parsed_manpages: {total_manpages:,}")
    print(f"  iterations:      {args.iterations:,}")

    sample_size = min(200, total_mappings)
    srcs_single, srcs_multi, srcs_subcmd = collect_sample_srcs(conn, sample_size)
    print(
        f"  sample: {len(srcs_single)} single-dst, "
        f"{len(srcs_multi)} multi-dst, "
        f"{len(srcs_subcmd)} subcommand"
    )

    # --- Without index ---
    conn.execute("DROP INDEX IF EXISTS idx_mappings_src")
    without = run_suite(
        conn,
        "WITHOUT idx_mappings_src",
        srcs_single,
        srcs_multi,
        srcs_subcmd,
        args.iterations,
    )

    # --- With index ---
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mappings_src ON mappings(src)")
    with_ = run_suite(
        conn,
        "WITH idx_mappings_src",
        srcs_single,
        srcs_multi,
        srcs_subcmd,
        args.iterations,
    )

    # --- Comparison ---
    print(f"\n{'=' * 60}")
    print("  COMPARISON (speedup = without / with)")
    print(f"{'=' * 60}")
    for query_name in without:
        w = without[query_name]
        wi = with_[query_name]
        speedup = w["mean_us"] / wi["mean_us"] if wi["mean_us"] > 0 else float("inf")
        saved = w["mean_us"] - wi["mean_us"]
        print(
            f"  {query_name}:\n"
            f"    without: {w['mean_us']:.1f}us  ->  with: {wi['mean_us']:.1f}us  "
            f"({speedup:.2f}x, saved {saved:.1f}us/query)"
        )

    # Leave index in place
    conn.close()
    print(f"\nNote: idx_mappings_src has been left in place on {args.db_path}")


if __name__ == "__main__":
    main()
