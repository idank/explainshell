"""LLM extractor benchmark tool.

Runs the LLM extractor on a corpus of manpages and produces a metrics
report.  Reports are stored with timestamps in a report directory so you
can track benchmark results over time.

A default corpus of 10 manpages lives in tests/regression/llm-bench/manpages/
covering tiny-to-huge pages, 1-6 chunks, dashless_opts, nested_cmd, and aliases.

Usage:
    # Run benchmark on the default corpus (auto-saves to report directory):
    python tools/llm_bench.py run --model openai/gpt-5-mini

    # Run benchmark on specific files:
    python tools/llm_bench.py run --model openai/gpt-5-mini path/to/file.1.gz ...

    # Run with batch API:
    python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50

    # Run with a description tag:
    python tools/llm_bench.py run --model openai/gpt-5-mini -d 'prompt tweak v2'

    # Compare the two most recent reports:
    python tools/llm_bench.py compare

    # Compare against a specific baseline:
    python tools/llm_bench.py compare --baseline tests/regression/llm-bench/20260328-064159/report.json

    # Compare two specific reports:
    python tools/llm_bench.py compare baseline.json current.json

    # List all saved reports:
    python tools/llm_bench.py list
"""

import argparse
import datetime
import glob
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from explainshell.extraction import ExtractorConfig, ExtractionOutcome, make_extractor
from explainshell.extraction.manifest import FileBatchManifestWriter
from explainshell.extraction.types import ExtractionResult
from explainshell.extraction.runner import run
from explainshell.util import collect_gz_files, git_metadata as _git_metadata

logger = logging.getLogger("explainshell.tools.llm_bench")

_RED = "\033[31m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

DEFAULT_REPORT_DIR = "tests/regression/llm-bench"
DEFAULT_CORPUS_DIR = "tests/regression/llm-bench/manpages"


def _basename(gz_path: str) -> str:
    return os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]


def _auto_output_path(report_dir: str) -> str:
    """Generate a timestamped output path in the report directory."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(report_dir, ts)
    os.makedirs(run_dir, exist_ok=True)
    return os.path.join(run_dir, "report.json")


def _list_reports(report_dir: str) -> list[str]:
    """Return report files sorted newest first (by filename)."""
    pattern = os.path.join(report_dir, "*/report.json")
    return sorted(glob.glob(pattern), reverse=True)


def run_bench(args: argparse.Namespace) -> int:
    gz_files = collect_gz_files(args.files or [DEFAULT_CORPUS_DIR])
    if not gz_files:
        print("No .gz files found.", file=sys.stderr)
        return 1

    if not args.model:
        print("error: --model is required", file=sys.stderr)
        return 1

    if args.batch is not None and args.batch < 1:
        print("error: --batch must be >= 1", file=sys.stderr)
        return 1

    logger.info("benchmarking %d file(s)...", len(gz_files))

    output = args.output or _auto_output_path(args.report_dir)
    run_dir = os.path.dirname(output)
    os.makedirs(run_dir, exist_ok=True)

    config = ExtractorConfig(model=args.model, run_dir=run_dir, debug=True)
    extractor = make_extractor("llm", config)

    # Build per-file metrics via on_result callback.
    file_metrics: dict[str, dict] = {}

    def _on_result(gz_path: str, fe: ExtractionResult) -> None:
        name = _basename(fe.gz_path)
        entry: dict = {"file": os.path.basename(fe.gz_path)}

        if fe.outcome == ExtractionOutcome.SUCCESS and fe.mp:
            entry["success"] = True
            entry["n_options"] = len(fe.mp.options)
            entry["dashless_opts"] = fe.mp.dashless_opts
            entry["n_aliases"] = len(fe.mp.aliases)
            entry["has_synopsis"] = bool(fe.mp.synopsis)
            entry["n_chunks"] = fe.stats.chunks
            entry["plain_text_len"] = fe.stats.plain_text_len
        elif fe.outcome == ExtractionOutcome.SKIPPED:
            entry["success"] = False
            entry["error"] = fe.error or "skipped"
            entry["n_chunks"] = fe.stats.chunks
            entry["plain_text_len"] = fe.stats.plain_text_len
        else:
            entry["success"] = False
            entry["error"] = fe.error or "extraction failed"
            entry["n_chunks"] = fe.stats.chunks
            entry["plain_text_len"] = fe.stats.plain_text_len
            entry["n_options"] = 0

        file_metrics[name] = entry

    manifest = None
    if args.batch is not None:
        manifest_path = os.path.join(run_dir, "batch-manifest.json")
        manifest = FileBatchManifestWriter(
            manifest_path, model=args.model, batch_size=args.batch
        )

    t0 = time.monotonic()
    result = run(
        extractor,
        gz_files,
        batch_size=args.batch,
        jobs=args.jobs,
        on_result=_on_result,
        manifest=manifest,
    )
    elapsed = time.monotonic() - t0

    # Build aggregate.
    all_files = list(file_metrics.values())
    extracted = [f for f in all_files if f.get("success") is True]
    failed_files = [f for f in all_files if f.get("success") is False]

    agg = {
        "total_files": len(all_files),
        "extracted_files": len(extracted),
        "failed_files": len(failed_files),
        "total_options": sum(f.get("n_options", 0) for f in extracted),
        "zero_option_pages": sum(1 for f in extracted if f.get("n_options", 0) == 0),
        "multi_chunk_pages": sum(1 for f in all_files if f.get("n_chunks", 0) > 1),
        "total_chunks": sum(f.get("n_chunks", 0) for f in all_files),
        "malformed_options": result.stats.malformed_options,
        "normalized_options": result.stats.normalized_options,
        "dropped_empty": result.stats.dropped_empty,
        "deduped_options": result.stats.deduped_options,
        "input_tokens": result.stats.input_tokens,
        "output_tokens": result.stats.output_tokens,
        "reasoning_tokens": result.stats.reasoning_tokens,
        "elapsed_seconds": round(elapsed, 1),
    }

    report: dict = {
        "model": args.model,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git": _git_metadata(),
        "batch_mode": args.batch is not None,
        "batch_size": args.batch,
        "jobs": args.jobs,
        "aggregate": agg,
        "files": file_metrics,
    }
    if args.description:
        report["description"] = args.description

    _print_summary(report)

    with open(output, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"  Run directory: {run_dir}")
    print(f"  Report saved to {output}")
    print()

    return 0


def _print_summary(report: dict) -> None:
    agg = report["aggregate"]
    git = report.get("git", {})

    print()
    print(f"  {_BOLD}LLM Bench Report{_RESET}")
    print(f"  Model: {report['model']}")
    print(f"  Timestamp: {report['timestamp']}")

    if git.get("commit_short"):
        dirty_marker = " (dirty)" if git.get("dirty") else ""
        print(f"  Git: {git['commit_short']}{dirty_marker}")

    if report.get("description"):
        print(f"  Description: {report['description']}")

    if report.get("batch_mode"):
        print(f"  Batch size: {report.get('batch_size')}")
    else:
        print("  Batch: off")

    print()

    rows = [
        ("Total files", agg["total_files"]),
        ("Extracted files", agg["extracted_files"]),
        ("Failed files", agg["failed_files"]),
        ("Total options", agg["total_options"]),
        ("Malformed options", agg["malformed_options"]),
        ("Normalized options", agg.get("normalized_options", 0)),
        ("Dropped empty", agg.get("dropped_empty", 0)),
        ("Deduped options", agg["deduped_options"]),
        ("Zero-option pages", agg["zero_option_pages"]),
        ("Multi-chunk pages", agg["multi_chunk_pages"]),
        ("Total chunks", agg["total_chunks"]),
        ("Input tokens", f"{agg['input_tokens']:,}"),
        ("Output tokens", f"{agg['output_tokens']:,}"),
        ("Reasoning tokens", f"{agg['reasoning_tokens']:,}"),
        ("Elapsed", f"{agg['elapsed_seconds']}s"),
    ]

    for label, val in rows:
        print(f"  {label:<22} {val}")

    print()


def _format_report_label(report: dict) -> str:
    """Format a one-line label for a report (model @ timestamp (git))."""
    label = f"{report['model']} @ {report['timestamp']}"
    git = report.get("git", {})
    if git.get("commit_short"):
        dirty = ", dirty" if git.get("dirty") else ""
        label += f" ({git['commit_short']}{dirty})"
    return label


def compare_reports(args: argparse.Namespace) -> int:
    # Determine which files to compare.
    if len(args.reports) == 2:
        if args.baseline:
            print(
                "error: --baseline cannot be used with two positional reports.",
                file=sys.stderr,
            )
            return 1
        baseline_path, current_path = args.reports
    elif len(args.reports) == 0:
        reports = _list_reports(args.report_dir)
        if args.baseline:
            baseline_path = args.baseline
            if len(reports) < 1:
                print(
                    f"No reports in {args.report_dir} to compare.",
                    file=sys.stderr,
                )
                return 1
            current_path = reports[0]  # newest
        else:
            if len(reports) < 2:
                print(
                    f"Need at least 2 reports in {args.report_dir} to compare. "
                    f"Found {len(reports)}.",
                    file=sys.stderr,
                )
                return 1
            current_path = reports[0]  # newest
            baseline_path = reports[1]  # second newest
    else:
        print("Expected 0 or 2 report files.", file=sys.stderr)
        return 1

    with open(baseline_path) as f:
        baseline = json.load(f)
    with open(current_path) as f:
        current = json.load(f)

    b_agg = baseline["aggregate"]
    c_agg = current["aggregate"]

    print()
    print(f"  {_BOLD}LLM Bench Comparison{_RESET}")
    print(f"  Baseline: {_format_report_label(baseline)}")
    if baseline.get("description"):
        print(f"            {baseline['description']}")
    print(f"  Current:  {_format_report_label(current)}")
    if current.get("description"):
        print(f"            {current['description']}")
    print()

    all_metrics = [
        ("total_files", "Total files", None),
        ("extracted_files", "Extracted files", True),
        ("failed_files", "Failed files", False),
        ("total_options", "Total options", True),
        ("malformed_options", "Malformed options", False),
        ("normalized_options", "Normalized options", None),
        ("dropped_empty", "Dropped empty", False),
        ("deduped_options", "Deduped options", False),
        ("zero_option_pages", "Zero-option pages", False),
        ("multi_chunk_pages", "Multi-chunk pages", None),
        ("total_chunks", "Total chunks", None),
        ("input_tokens", "Input tokens", False),
        ("output_tokens", "Output tokens", False),
        ("reasoning_tokens", "Reasoning tokens", False),
    ]

    metrics = [
        (k, label, h) for k, label, h in all_metrics if k in b_agg and k in c_agg
    ]
    regressions: list[tuple] = []

    print(f"  {'Metric':<22} {'Baseline':>10} {'Current':>10} {'Delta':>8}")
    print(f"  {'-' * 54}")

    for key, label, higher_is_better in metrics:
        b_val = b_agg[key]
        c_val = c_agg[key]
        delta = c_val - b_val

        if delta == 0:
            marker = ""
        elif higher_is_better is True:
            marker = (
                f" {_GREEN}(+){_RESET}" if delta > 0 else f" {_RED}REGRESSION{_RESET}"
            )
        elif higher_is_better is False:
            marker = (
                f" {_GREEN}(+){_RESET}" if delta < 0 else f" {_RED}REGRESSION{_RESET}"
            )
        else:
            marker = ""

        if higher_is_better is not None and (
            (higher_is_better and delta < 0) or (not higher_is_better and delta > 0)
        ):
            regressions.append((label, b_val, c_val, delta))

        delta_str = f"{delta:+d}" if delta != 0 else "-"
        print(f"  {label:<22} {b_val:>10} {c_val:>10} {delta_str:>8}{marker}")

    b_files = baseline.get("files", {})
    c_files = current.get("files", {})
    all_names = sorted(set(b_files) | set(c_files))

    changed_files: list[tuple] = []
    for name in all_names:
        b_opts = b_files.get(name, {}).get("n_options")
        c_opts = c_files.get(name, {}).get("n_options")

        if b_opts is None and c_opts is None:
            continue
        if b_opts == c_opts:
            continue

        if b_opts is None:
            changed_files.append((name, "-", c_opts, "(new)"))
        elif c_opts is None:
            changed_files.append((name, b_opts, "-", "(removed)"))
        else:
            changed_files.append((name, b_opts, c_opts, f"{c_opts - b_opts:+d}"))

    if changed_files:
        print()
        print(f"  {_BOLD}Per-file option count changes:{_RESET}")
        print(f"  {'File':<22} {'Baseline':>10} {'Current':>10} {'Delta':>8}")
        print(f"  {'-' * 54}")
        for name, b_val, c_val, delta_str in changed_files:
            print(f"  {name:<22} {b_val:>10} {c_val:>10} {delta_str:>8}")

    print()
    if regressions:
        print(f"  {_RED}{_BOLD}REGRESSIONS DETECTED:{_RESET}")
        for label, b_val, c_val, delta in regressions:
            print(f"  {_RED}  {label}: {b_val} -> {c_val} ({delta:+d}){_RESET}")
        print()
        return 1

    print(f"  {_GREEN}No regressions detected.{_RESET}")
    print()
    return 0


def list_reports(args: argparse.Namespace) -> int:
    reports = _list_reports(args.report_dir)
    if not reports:
        print(f"No reports found in {args.report_dir}.", file=sys.stderr)
        return 1

    # Pre-read all reports to compute column widths.
    rows: list[tuple[str, str, str, str, str, str]] = []
    for path in reports:
        with open(path) as f:
            report = json.load(f)

        git = report.get("git", {})
        commit = git.get("commit_short", "?")
        dirty = "*" if git.get("dirty") else ""
        git_str = f"{commit}{dirty}"

        ts = report.get("timestamp", "?")
        if len(ts) > 19:
            ts = ts[:19]

        model = report.get("model", "?")
        agg = report.get("aggregate", {})
        opts = str(agg.get("total_options", "?"))
        desc = report.get("description", "")

        rows.append((path, ts, git_str, model, opts, desc))

    pw = max(len(r[0]) for r in rows)
    mw = max(len(r[3]) for r in rows)

    print()
    print(f"  {_BOLD}LLM Bench Reports{_RESET} ({args.report_dir})")
    print()
    header = (
        f"  {'Path':<{pw}}  {'Date':<19}  {'Git':<10}  {'Model':<{mw}}  "
        f"{'Options':>7}  Description"
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    for path, ts, git_str, model, opts, desc in rows:
        print(
            f"  {path:<{pw}}  {ts:<19}  {git_str:<10}  {model:<{mw}}  {opts:>7}  {desc}"
        )

    print()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM extractor benchmark tool.")
    parser.add_argument("--log", default="INFO", help="Log level (default: INFO)")
    parser.add_argument(
        "--report-dir",
        default=DEFAULT_REPORT_DIR,
        help=f"Report directory (default: {DEFAULT_REPORT_DIR})",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run benchmark and produce a metrics report")
    run_p.add_argument("--model", help="LLM model (e.g. openai/gpt-5-mini)")
    run_p.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Use provider batch API with this batch size (e.g. 50)",
    )
    run_p.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        help="Number of concurrent batch jobs (default: 1)",
    )
    run_p.add_argument(
        "--output",
        "-o",
        help="Output JSON file path (default: auto-generated in report dir)",
    )
    run_p.add_argument(
        "--description",
        "-d",
        help="Short description of changes for this run",
    )
    run_p.add_argument(
        "files",
        nargs="*",
        help=f".gz files or directories (default: {DEFAULT_CORPUS_DIR})",
    )

    cmp_p = sub.add_parser("compare", help="Compare two benchmark reports")
    cmp_p.add_argument(
        "--baseline",
        "-b",
        help="Baseline report path to compare against",
    )
    cmp_p.add_argument(
        "reports",
        nargs="*",
        help="Two report files to compare (default: latest two from report dir)",
    )

    sub.add_parser("list", help="List all saved reports")

    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    log_level = getattr(logging, args.log.upper())
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("explainshell").setLevel(log_level)

    if args.command == "run":
        sys.exit(run_bench(args))
    elif args.command == "compare":
        sys.exit(compare_reports(args))
    elif args.command == "list":
        sys.exit(list_reports(args))
    else:
        parser.print_help()
        sys.exit(1)
