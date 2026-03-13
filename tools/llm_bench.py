"""LLM extractor benchmark tool.

Runs the LLM extractor on a corpus of manpages and produces a metrics
report.  Compare before/after reports when making changes to the LLM
extractor to catch regressions.

Usage:
    # Run benchmark, save report:
    python tools/llm_bench.py run --model openai/gpt-5-mini tests/regression/manpages/

    # Compare two reports:
    python tools/llm_bench.py compare baseline.json current.json
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

from explainshell.extraction import ExtractorConfig, FileOutcome, make_extractor
from explainshell.extraction.runner import run_batch, run_sequential

logger = logging.getLogger(__name__)

_RED = "\033[31m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _collect_gz_files(paths: list[str]) -> list[str]:
    result: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            result.extend(
                sorted(glob.glob(os.path.join(path, "**", "*.gz"), recursive=True))
            )
        else:
            result.append(path)
    return [os.path.abspath(p) for p in result]


def _basename(gz_path: str) -> str:
    return os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]


def run_bench(args: argparse.Namespace) -> int:
    gz_files = _collect_gz_files(args.files)
    if not gz_files:
        print("No .gz files found.", file=sys.stderr)
        return 1

    if not args.model:
        print("error: --model is required", file=sys.stderr)
        return 1

    logger.info("benchmarking %d file(s)...", len(gz_files))

    config = ExtractorConfig(model=args.model)
    extractor = make_extractor("llm", config)

    t0 = time.monotonic()
    if args.batch is not None:
        result = run_batch(extractor, gz_files, batch_size=args.batch)
    else:
        result = run_sequential(extractor, gz_files)
    elapsed = time.monotonic() - t0

    # Build per-file metrics.
    file_metrics: dict[str, dict] = {}
    for fe in result.files:
        name = _basename(fe.gz_path)
        entry: dict = {"file": os.path.basename(fe.gz_path)}

        if fe.outcome == FileOutcome.SUCCESS and fe.result:
            entry["success"] = True
            entry["n_options"] = len(fe.result.mp.options)
            entry["dashless_opts"] = fe.result.mp.dashless_opts
            entry["n_aliases"] = len(fe.result.mp.aliases)
            entry["has_synopsis"] = bool(fe.result.mp.synopsis)
            entry["n_chunks"] = fe.result.stats.chunks
            entry["plain_text_len"] = fe.result.stats.plain_text_len
        elif fe.outcome == FileOutcome.SKIPPED:
            entry["success"] = False
            entry["error"] = fe.error or "skipped"
            entry["n_chunks"] = fe.stats.chunks if fe.stats else 0
            entry["plain_text_len"] = fe.stats.plain_text_len if fe.stats else 0
        else:
            entry["success"] = False
            entry["error"] = fe.error or "extraction failed"
            entry["n_chunks"] = fe.stats.chunks if fe.stats else 0
            entry["plain_text_len"] = fe.stats.plain_text_len if fe.stats else 0
            entry["n_options"] = 0

        file_metrics[name] = entry

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
        "input_tokens": result.stats.input_tokens,
        "output_tokens": result.stats.output_tokens,
        "elapsed_seconds": round(elapsed, 1),
    }

    report = {
        "model": args.model,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "aggregate": agg,
        "files": file_metrics,
    }

    _print_summary(report)

    output = args.output
    if output:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        logger.info("report saved to %s", output)

    return 0


def _print_summary(report: dict) -> None:
    agg = report["aggregate"]
    print()
    print(f"  {_BOLD}LLM Bench Report{_RESET}")
    print(f"  Model: {report['model']}")
    print(f"  Timestamp: {report['timestamp']}")
    print()

    rows = [
        ("Total files", agg["total_files"]),
        ("Extracted files", agg["extracted_files"]),
        ("Failed files", agg["failed_files"]),
        ("Total options", agg["total_options"]),
        ("Zero-option pages", agg["zero_option_pages"]),
        ("Multi-chunk pages", agg["multi_chunk_pages"]),
        ("Total chunks", agg["total_chunks"]),
        ("Input tokens", f"{agg['input_tokens']:,}"),
        ("Output tokens", f"{agg['output_tokens']:,}"),
        ("Elapsed", f"{agg['elapsed_seconds']}s"),
    ]

    for label, val in rows:
        print(f"  {label:<22} {val}")

    print()


def compare_reports(args: argparse.Namespace) -> int:
    with open(args.baseline) as f:
        baseline = json.load(f)
    with open(args.current) as f:
        current = json.load(f)

    b_agg = baseline["aggregate"]
    c_agg = current["aggregate"]

    print()
    print(f"  {_BOLD}LLM Bench Comparison{_RESET}")
    print(f"  Baseline: {baseline['model']} @ {baseline['timestamp']}")
    print(f"  Current:  {current['model']} @ {current['timestamp']}")
    print()

    all_metrics = [
        ("total_files", "Total files", None),
        ("extracted_files", "Extracted files", True),
        ("failed_files", "Failed files", False),
        ("total_options", "Total options", True),
        ("zero_option_pages", "Zero-option pages", False),
        ("multi_chunk_pages", "Multi-chunk pages", None),
        ("total_chunks", "Total chunks", None),
        ("input_tokens", "Input tokens", None),
        ("output_tokens", "Output tokens", None),
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM extractor benchmark tool.")
    parser.add_argument("--log", default="INFO", help="Log level (default: INFO)")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run benchmark and produce a metrics report")
    run_p.add_argument("--model", help="LLM model (e.g. openai/gpt-5-mini)")
    run_p.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Use provider batch API with this batch size (e.g. 50)",
    )
    run_p.add_argument("--output", "-o", help="Output JSON file path")
    run_p.add_argument("files", nargs="+", help=".gz files or directories")

    cmp_p = sub.add_parser("compare", help="Compare two benchmark reports")
    cmp_p.add_argument("baseline", help="Baseline report JSON")
    cmp_p.add_argument("current", help="Current report JSON")

    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log.upper()),
        stream=sys.stdout,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "run":
        sys.exit(run_bench(args))
    elif args.command == "compare":
        sys.exit(compare_reports(args))
    else:
        parser.print_help()
        sys.exit(1)
