#!/usr/bin/env python3
"""Evaluate the LLM option extractor on a real-manpage corpus.

Mirrors the layout of the sibling render eval at ``tests/evals/render/``:
``run`` extracts a corpus and writes ``summary.json`` plus per-page artifacts
under ``markdown/``, ``prompts/``, ``responses/``; ``compare`` flags suspicious
changes between two runs; ``list`` enumerates saved runs.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from explainshell.extraction import (  # noqa: E402
    ExtractionOutcome,
    ExtractorConfig,
    make_extractor,
)
from explainshell.extraction.manifest import FileBatchManifestWriter  # noqa: E402
from explainshell.extraction.runner import run as run_extraction  # noqa: E402
from explainshell.extraction.types import ExtractionResult  # noqa: E402
from explainshell.util import collect_gz_files  # noqa: E402
from tests.evals._common import (  # noqa: E402
    _format_delta,
    _get_metric,
    _git_metadata,
    _load_summary,
    _page_map,
    _read_corpus,
    _repo_relative,
    _safe_name,
    _write_json,
)

logger = logging.getLogger("explainshell.tests.evals.llm")

_RED = "\033[31m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

EVAL_DIR = _HERE.parent
DEFAULT_CORPUS = EVAL_DIR / "corpus.txt"
DEFAULT_RUNS_DIR = EVAL_DIR / "runs"
DEFAULT_MODEL = "openai/gpt-5-mini"


def _basename(gz_path: str) -> str:
    return os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]


def _relocate_artifacts(raw_dir: Path, run_dir: Path, basename: str, safe: str) -> None:
    """Move per-file artifacts the LLM extractor wrote to ``raw_dir`` into the
    eval's named subdirs, renaming the basename prefix to ``safe``."""
    md = raw_dir / f"{basename}.md"
    if md.exists():
        shutil.move(str(md), str(run_dir / "markdown" / f"{safe}.md"))
    for src in raw_dir.iterdir():
        if not src.name.startswith(f"{basename}."):
            continue
        suffix = src.name[len(basename) + 1 :]
        if suffix.endswith("prompt.json"):
            dst = run_dir / "prompts" / f"{safe}.{suffix}"
        elif suffix.endswith("response.txt") or suffix.endswith("failed-response.txt"):
            dst = run_dir / "responses" / f"{safe}.{suffix}"
        else:
            continue
        shutil.move(str(src), str(dst))


def run_bench(args: argparse.Namespace) -> int:
    if args.batch is not None and args.batch < 1:
        print("error: --batch must be >= 1", file=sys.stderr)
        return 1

    if args.paths:
        gz_files = collect_gz_files([str(p) for p in args.paths])
    else:
        corpus_paths = _read_corpus(Path(args.corpus))
        if not corpus_paths:
            print(f"corpus {args.corpus} is empty", file=sys.stderr)
            return 1
        gz_files = [os.path.abspath(str(p)) for p in corpus_paths]

    if not gz_files:
        print("No .gz files found.", file=sys.stderr)
        return 1

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label).strip("-") or "llm"
    run_dir = Path(args.output or DEFAULT_RUNS_DIR / f"{timestamp}-{label}")
    raw_dir = run_dir / "raw"
    for subdir in ("markdown", "prompts", "responses", "raw"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    logger.info("benchmarking %d file(s)...", len(gz_files))

    config = ExtractorConfig(model=args.model, run_dir=str(raw_dir), debug=True)
    extractor = make_extractor("llm", config)

    pages_by_safe: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []

    def _on_result(gz_path: str, fe: ExtractionResult) -> None:
        rel_path = _repo_relative(Path(fe.gz_path))
        basename = _basename(fe.gz_path)
        safe = _safe_name(rel_path)

        extraction: dict[str, Any] = {
            "n_chunks": fe.stats.chunks,
            "plain_text_len": fe.stats.plain_text_len,
        }
        if fe.outcome == ExtractionOutcome.SUCCESS and fe.mp:
            extraction["success"] = True
            extraction["n_options"] = len(fe.mp.options)
            extraction["dashless_opts"] = fe.mp.dashless_opts
            extraction["n_aliases"] = len(fe.mp.aliases)
            extraction["has_synopsis"] = bool(fe.mp.synopsis)
        else:
            extraction["success"] = False
            extraction["n_options"] = 0
            error = fe.error or (
                "skipped"
                if fe.outcome == ExtractionOutcome.SKIPPED
                else "extraction failed"
            )
            extraction["error"] = error
            failures.append({"path": rel_path, "error": error})

        # Per-page tokens are populated for realtime calls; the OpenAI batch
        # API only returns aggregate usage, so batch runs leave these at 0.
        page = {
            "path": rel_path,
            "safe_name": safe,
            "metrics": {
                "extraction": extraction,
                "tokens": {
                    "input": fe.stats.input_tokens,
                    "output": fe.stats.output_tokens,
                    "reasoning": fe.stats.reasoning_tokens,
                },
            },
        }
        pages_by_safe[safe] = page

        try:
            _relocate_artifacts(raw_dir, run_dir, basename, safe)
        except OSError as exc:
            logger.warning("failed to relocate artifacts for %s: %s", basename, exc)

    manifest = None
    if args.batch is not None:
        manifest_path = str(run_dir / "batch-manifest.json")
        manifest = FileBatchManifestWriter(
            manifest_path, model=args.model, batch_size=args.batch
        )

    t0 = time.monotonic()
    result = run_extraction(
        extractor,
        gz_files,
        batch_size=args.batch,
        jobs=args.jobs,
        on_result=_on_result,
        manifest=manifest,
    )
    elapsed = time.monotonic() - t0

    pages = sorted(pages_by_safe.values(), key=lambda p: p["path"])
    extracted = [p for p in pages if p["metrics"]["extraction"].get("success")]
    failed = [p for p in pages if not p["metrics"]["extraction"].get("success")]

    aggregate = {
        "total_files": len(pages),
        "extracted_files": len(extracted),
        "failed_files": len(failed),
        "total_options": sum(
            p["metrics"]["extraction"].get("n_options", 0) for p in extracted
        ),
        "zero_option_pages": sum(
            1 for p in extracted if p["metrics"]["extraction"].get("n_options", 0) == 0
        ),
        "multi_chunk_pages": sum(
            1 for p in pages if p["metrics"]["extraction"].get("n_chunks", 0) > 1
        ),
        "total_chunks": sum(
            p["metrics"]["extraction"].get("n_chunks", 0) for p in pages
        ),
        "malformed_options": result.stats.malformed_options,
        "normalized_options": result.stats.normalized_options,
        "dropped_empty": result.stats.dropped_empty,
        "deduped_options": result.stats.deduped_options,
        "input_tokens": result.stats.input_tokens,
        "output_tokens": result.stats.output_tokens,
        "reasoning_tokens": result.stats.reasoning_tokens,
        "elapsed_seconds": round(elapsed, 1),
    }

    summary: dict[str, Any] = {
        "label": args.label,
        "timestamp": datetime.now(UTC).isoformat(),
        "git": _git_metadata(),
        "model": args.model,
        "batch_mode": args.batch is not None,
        "batch_size": args.batch,
        "jobs": args.jobs,
        "description": args.description or "",
        "corpus": [_repo_relative(Path(p)) for p in gz_files],
        "page_count": len(pages),
        "failure_count": len(failures),
        "failures": failures,
        "aggregate": aggregate,
        "pages": pages,
    }
    _write_json(run_dir / "summary.json", summary)

    try:
        if raw_dir.exists() and not any(raw_dir.iterdir()):
            raw_dir.rmdir()
    except OSError:
        pass

    _print_summary(summary)
    print(f"  Run directory: {run_dir}")
    print(f"  Summary: {run_dir / 'summary.json'}")
    print()

    return 0


def _print_summary(summary: dict[str, Any]) -> None:
    agg = summary["aggregate"]
    git = summary.get("git") or {}

    print()
    print(f"  {_BOLD}LLM Eval Run{_RESET}")
    print(f"  Label: {summary['label']}")
    print(f"  Model: {summary['model']}")
    print(f"  Timestamp: {summary['timestamp']}")
    if git.get("commit"):
        dirty_marker = " (dirty)" if git.get("dirty") else ""
        print(f"  Git: {git['commit']}{dirty_marker}")
    if summary.get("description"):
        print(f"  Description: {summary['description']}")
    if summary.get("batch_mode"):
        print(f"  Batch size: {summary.get('batch_size')}")
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


# Suspicious-change checks for ``compare``.  Tokens deliberately absent: cross-
# model token deltas would otherwise dominate the verdict on a model swap.
_PAGE_CHECKS: dict[str, tuple[float, str]] = {
    # key: (tolerance, direction); direction is "down" (drop is bad), "up"
    # (rise is bad), or "any" (any change is flagged).
    "extraction.success": (0.0, "any"),
    "extraction.n_options": (0.0, "down"),
}

_AGGREGATE_CHECKS: list[tuple[str, str, str]] = [
    # (key, label, direction)
    ("failed_files", "Failed files", "up"),
    ("malformed_options", "Malformed options", "up"),
    ("zero_option_pages", "Zero-option pages", "up"),
    ("extracted_files", "Extracted files", "down"),
    ("total_options", "Total options", "down"),
]

_AGGREGATE_DELTA_KEYS: list[tuple[str, str]] = [
    ("total_files", "Total files"),
    ("extracted_files", "Extracted files"),
    ("failed_files", "Failed files"),
    ("total_options", "Total options"),
    ("malformed_options", "Malformed options"),
    ("normalized_options", "Normalized options"),
    ("dropped_empty", "Dropped empty"),
    ("deduped_options", "Deduped options"),
    ("zero_option_pages", "Zero-option pages"),
    ("multi_chunk_pages", "Multi-chunk pages"),
    ("total_chunks", "Total chunks"),
    ("input_tokens", "Input tokens"),
    ("output_tokens", "Output tokens"),
    ("reasoning_tokens", "Reasoning tokens"),
]


def _suspicious_page_changes(
    old: dict[str, Any] | None, new: dict[str, Any] | None
) -> list[str]:
    if old is None:
        return ["page added"]
    if new is None:
        return ["page removed"]

    reasons: list[str] = []
    for key, (tolerance, direction) in _PAGE_CHECKS.items():
        before = _get_metric(old, key)
        after = _get_metric(new, key)
        if before == after:
            continue

        # extraction.success is a bool; _get_metric returns 0 for it. Read
        # directly so flips surface explicitly.
        if key == "extraction.success":
            b = bool(old.get("metrics", {}).get("extraction", {}).get("success"))
            a = bool(new.get("metrics", {}).get("extraction", {}).get("success"))
            if b != a:
                reasons.append(f"{key}: {b} -> {a}")
            continue

        delta = after - before
        if direction == "down" and delta < 0:
            flagged = True
        elif direction == "up" and delta > 0:
            flagged = True
        elif direction == "any":
            flagged = True
        else:
            flagged = False
        if not flagged:
            continue
        if tolerance > 0:
            denominator = max(abs(before), 1)
            if abs(delta) / denominator <= tolerance:
                continue
        reasons.append(f"{key}: {before} -> {after} ({delta:+})")
    return reasons


def _suspicious_aggregate(
    base_agg: dict[str, Any], curr_agg: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    for key, label, direction in _AGGREGATE_CHECKS:
        before = base_agg.get(key, 0)
        after = curr_agg.get(key, 0)
        if before == after:
            continue
        delta = after - before
        if direction == "down" and delta >= 0:
            continue
        if direction == "up" and delta <= 0:
            continue
        reasons.append(f"aggregate.{key} ({label}): {before} -> {after} ({delta:+})")
    return reasons


def _changed_page_metric_lines(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = [
        "extraction.n_options",
        "extraction.n_chunks",
        "extraction.plain_text_len",
        "extraction.n_aliases",
        "tokens.input",
        "tokens.output",
        "tokens.reasoning",
    ]
    changed: list[str] = []
    for key in keys:
        before = _get_metric(old, key)
        after = _get_metric(new, key)
        if before != after:
            changed.append(f"- `{key}`: {_format_delta(before, after)}")
    return changed


def _format_run_label(summary: dict[str, Any]) -> str:
    label = f"{summary.get('model', '?')} @ {summary.get('timestamp', '?')}"
    git = summary.get("git") or {}
    if git.get("commit"):
        dirty = ", dirty" if git.get("dirty") else ""
        label += f" ({git['commit']}{dirty})"
    return label


def compare_runs(args: argparse.Namespace) -> int:
    base_dir = Path(args.baseline)
    current_dir = Path(args.current)
    baseline = _load_summary(base_dir)
    current = _load_summary(current_dir)

    base_agg = baseline.get("aggregate", {})
    curr_agg = current.get("aggregate", {})

    base_pages = _page_map(baseline)
    curr_pages = _page_map(current)
    paths = sorted(set(base_pages) | set(curr_pages))

    suspicious_pages: dict[str, list[str]] = {}
    for path in paths:
        reasons = _suspicious_page_changes(base_pages.get(path), curr_pages.get(path))
        if reasons:
            suspicious_pages[path] = reasons
    suspicious_agg = _suspicious_aggregate(base_agg, curr_agg)

    print()
    print(f"  {_BOLD}LLM Eval Comparison{_RESET}")
    print(f"  Baseline: {base_dir} ({_format_run_label(baseline)})")
    if baseline.get("description"):
        print(f"            {baseline['description']}")
    print(f"  Current:  {current_dir} ({_format_run_label(current)})")
    if current.get("description"):
        print(f"            {current['description']}")
    print()

    print(f"  {'Aggregate':<22} {'Baseline':>12} {'Current':>12} {'Delta':>10}")
    print(f"  {'-' * 60}")
    for key, label in _AGGREGATE_DELTA_KEYS:
        b_val = base_agg.get(key, 0)
        c_val = curr_agg.get(key, 0)
        delta = c_val - b_val
        delta_str = f"{delta:+,}" if delta != 0 else "-"
        print(f"  {label:<22} {b_val:>12,} {c_val:>12,} {delta_str:>10}")
    print()

    changed_pages: list[tuple[str, list[str]]] = []
    for path in paths:
        old = base_pages.get(path)
        new = curr_pages.get(path)
        if old is None or new is None:
            continue
        lines = _changed_page_metric_lines(old, new)
        if lines:
            changed_pages.append((path, lines))

    if changed_pages:
        print(f"  {_BOLD}Per-page metric deltas:{_RESET}")
        for path, lines in changed_pages:
            print(f"    {path}")
            for line in lines:
                print(f"      {line[2:]}")  # drop leading '- '
        print()

    print(f"  {_BOLD}Suspicious structural changes:{_RESET}")
    if suspicious_agg or suspicious_pages:
        for reason in suspicious_agg:
            print(f"  {_RED}{reason}{_RESET}")
        for path, reasons in suspicious_pages.items():
            print(f"  {_RED}{path}{_RESET}")
            for reason in reasons:
                print(f"    {_RED}{reason}{_RESET}")
        print()
        return 1 if args.fail_on_suspicious else 0

    print(f"  {_GREEN}None detected.{_RESET}")
    print()
    return 0


def list_runs(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        print(f"No runs directory at {runs_dir}.", file=sys.stderr)
        return 1
    summaries = sorted(runs_dir.glob("*/summary.json"), reverse=True)
    if not summaries:
        print(f"No runs found in {runs_dir}.", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str, str, str]] = []
    for path in summaries:
        try:
            summary = _load_summary(path.parent)
        except (OSError, ValueError):
            continue
        git = summary.get("git") or {}
        commit = git.get("commit") or "?"
        dirty = "*" if git.get("dirty") else ""
        ts = (summary.get("timestamp") or "?")[:19]
        model = summary.get("model") or "?"
        opts = str(summary.get("aggregate", {}).get("total_options", "?"))
        desc = summary.get("description") or summary.get("label") or ""
        rows.append((str(path.parent), ts, f"{commit}{dirty}", model, opts, desc))

    pw = max(len(r[0]) for r in rows)
    mw = max(len(r[3]) for r in rows)

    print()
    print(f"  {_BOLD}LLM Eval Runs{_RESET} ({runs_dir})")
    print()
    header = (
        f"  {'Run':<{pw}}  {'Date':<19}  {'Git':<10}  {'Model':<{mw}}  "
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="INFO", help="log level (default: INFO)")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the LLM extractor and write a summary")
    run_p.add_argument("--label", required=True, help="human-readable run label")
    run_p.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})"
    )
    run_p.add_argument(
        "--batch",
        type=int,
        default=None,
        help="use provider batch API with this batch size (e.g. 50)",
    )
    run_p.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        help="number of concurrent batch jobs (default: 1)",
    )
    run_p.add_argument(
        "--description",
        "-d",
        default="",
        help="short description of changes for this run",
    )
    run_p.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="corpus file path")
    run_p.add_argument("--output", help="run output directory")
    run_p.add_argument(
        "paths", nargs="*", help="optional .gz files / dirs (overrides --corpus)"
    )
    run_p.set_defaults(func=run_bench)

    cmp_p = sub.add_parser("compare", help="compare two LLM eval runs")
    cmp_p.add_argument("baseline", help="baseline run directory")
    cmp_p.add_argument("current", help="current run directory")
    cmp_p.add_argument(
        "--fail-on-suspicious",
        action="store_true",
        help="exit non-zero when suspicious structural changes are detected",
    )
    cmp_p.set_defaults(func=compare_runs)

    list_p = sub.add_parser("list", help="list saved LLM eval runs")
    list_p.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help=f"runs directory (default: {DEFAULT_RUNS_DIR})",
    )
    list_p.set_defaults(func=list_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_level = getattr(logging, args.log.upper(), logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("explainshell").setLevel(log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
