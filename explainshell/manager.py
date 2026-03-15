"""
CLI entry point for man page extraction.

Usage:
    python -m explainshell.manager --mode <mode> [options] files...

Modes:
    source              Use the roff parser
    mandoc              Use mandoc -T tree parser
    llm:<model>         Use an LLM (e.g. llm:openai/gpt-4o)
    hybrid:<model>      Try tree parser first, fall back to LLM when confidence is low
"""

import argparse
import glob
import logging
import os
import sys
import threading
import time

from explainshell import config, errors, store
from explainshell.diff import format_diff
from explainshell.extraction import (
    BatchResult,
    ExtractorConfig,
    ExtractionOutcome,
    ExtractionResult,
    make_extractor,
)
from explainshell.extraction.runner import run_batch, run_parallel, run_sequential

logger = logging.getLogger(__name__)

# ANSI color helpers.
_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _fmt_tokens(n: int) -> str:
    """Format token count for display (e.g. 878K, 1.8M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _already_stored(s: store.Store, short_path: str, name: str) -> bool:
    try:
        results = s.find_man_page(name)
        return any(mp.source == short_path for mp in results)
    except errors.ProgramDoesNotExist:
        return False


def _collect_gz_files(paths: list[str]) -> list[str]:
    result: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            result.extend(
                os.path.abspath(f)
                for f in glob.glob(os.path.join(path, "**", "*.gz"), recursive=True)
            )
        else:
            result.append(os.path.abspath(path))
    return result


def _parse_mode(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a --mode value into (mode, model).

    Returns ("source", None), ("mandoc", None), ("llm", "<model>"),
    or ("hybrid", "<model>").

    Raises ValueError on invalid input.
    """
    if raw is None:
        return None, None
    if raw == "source":
        return "source", None
    if raw == "mandoc":
        return "mandoc", None
    if raw.startswith("llm:"):
        model = raw[4:]
        if not model:
            raise ValueError(
                "--mode llm:<model> requires a model name (e.g. llm:gpt-4o)"
            )
        return "llm", model
    if raw.startswith("hybrid:"):
        model = raw[7:]
        if not model:
            raise ValueError(
                "--mode hybrid:<model> requires a model name (e.g. hybrid:gpt-4o)"
            )
        return "hybrid", model
    raise ValueError(
        f"invalid --mode value: {raw!r} "
        f"(expected 'source', 'mandoc', 'llm:<model>', or 'hybrid:<model>')"
    )


def _parse_diff(
    raw: str | None,
) -> tuple[str | None, tuple | None, tuple | None]:
    """Parse a --diff value into a structured result.

    Returns:
        (None, None, None)                              for None/False (no diff)
        ("db", None, None)                              for "db"
        ("extractors", (modeA, modelA), (modeB, modelB))  for "A..B"

    Raises ValueError on invalid input.
    """
    if not raw:
        return (None, None, None)
    if raw == "db":
        return ("db", None, None)
    if ".." in raw:
        parts = raw.split("..", 1)
        left_mode, left_model = _parse_mode(parts[0])
        right_mode, right_model = _parse_mode(parts[1])
        return ("extractors", (left_mode, left_model), (right_mode, right_model))
    raise ValueError(
        f"invalid --diff value: {raw!r} "
        f"(expected 'db' or 'A..B' where A and B are extractor specs like 'source', 'mandoc', 'llm:<model>')"
    )


# ---------------------------------------------------------------------------
# Diff mode helpers
# ---------------------------------------------------------------------------


def _run_diff_extractors(
    gz_files: list[str],
    diff_left: tuple,
    diff_right: tuple,
    debug_dir: str | None,
) -> BatchResult:
    """Run --diff A..B mode: compare two extractors on each file."""
    left_mode, left_model = diff_left
    right_mode, right_model = diff_right
    label = f"{left_mode} vs {right_mode}"

    left_cfg = ExtractorConfig(model=left_model, fail_dir=debug_dir)
    right_cfg = ExtractorConfig(model=right_model, fail_dir=debug_dir)
    left_ext = make_extractor(left_mode, left_cfg)
    right_ext = make_extractor(right_mode, right_cfg)

    right_label = right_mode if not right_model else f"{right_mode} ({right_model})"
    logger.info("running %s extractor on %d file(s)...", left_mode, len(gz_files))
    left_batch = run_sequential(left_ext, gz_files)
    logger.info("running %s extractor on %d file(s)...", right_label, len(gz_files))
    right_batch = run_sequential(right_ext, gz_files)

    batch = BatchResult()
    for left_entry, right_entry in zip(left_batch.files, right_batch.files):
        gz_path = left_entry.gz_path
        short_path = config.source_from_path(gz_path)
        left_ok = left_entry.outcome == ExtractionOutcome.SUCCESS
        right_ok = right_entry.outcome == ExtractionOutcome.SUCCESS

        # Always accumulate stats from successful extractions, even when
        # the other side failed — the tokens were consumed either way.
        if left_ok:
            batch.stats += left_entry.stats
        if right_ok:
            batch.stats += right_entry.stats

        if not left_ok or not right_ok:
            logger.info("=== %s (%s) ===", short_path, label)
            if not left_ok:
                logger.info(
                    "  %s(%s extractor %s: %s)%s",
                    _DIM,
                    left_mode,
                    left_entry.outcome.value,
                    left_entry.error,
                    _RESET,
                )
            if not right_ok:
                logger.info(
                    "  %s(%s extractor %s: %s)%s",
                    _DIM,
                    right_label,
                    right_entry.outcome.value,
                    right_entry.error,
                    _RESET,
                )
            # Use the more severe outcome (FAILED > SKIPPED).
            if (
                left_entry.outcome == ExtractionOutcome.FAILED
                or right_entry.outcome == ExtractionOutcome.FAILED
            ):
                failed_side = (
                    left_entry
                    if left_entry.outcome == ExtractionOutcome.FAILED
                    else right_entry
                )
                batch.files.append(
                    ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.FAILED,
                        error=failed_side.error,
                    )
                )
            else:
                skipped_side = left_entry if not left_ok else right_entry
                batch.files.append(
                    ExtractionResult(
                        gz_path=gz_path,
                        outcome=ExtractionOutcome.SKIPPED,
                        error=skipped_side.error,
                    )
                )
            continue

        logger.info("=== %s (%s) ===", short_path, label)
        for line in format_diff(left_entry.mp, right_entry.mp):
            logger.info(line)

        li = left_entry.stats.input_tokens
        ri = right_entry.stats.input_tokens
        if li or ri:
            lo = left_entry.stats.output_tokens
            ro = right_entry.stats.output_tokens
            logger.info("  %stokens:%s", _BOLD, _RESET)
            logger.info(
                "    %s: %s in / %s out",
                left_mode,
                _fmt_tokens(li),
                _fmt_tokens(lo),
            )
            logger.info(
                "    %s: %s in / %s out",
                right_mode,
                _fmt_tokens(ri),
                _fmt_tokens(ro),
            )

        batch.files.append(
            ExtractionResult(
                gz_path=gz_path,
                outcome=ExtractionOutcome.SUCCESS,
            )
        )

    return batch


def _run_diff_db(
    gz_files: list[str],
    mode: str,
    model: str | None,
    debug_dir: str | None,
    dry_run: bool,
    s: store.Store,
) -> BatchResult:
    """Run --diff db mode: compare fresh extraction against the DB."""
    _debug_dir = debug_dir if dry_run else None
    cfg = ExtractorConfig(model=model, debug_dir=_debug_dir, fail_dir=debug_dir)
    ext = make_extractor(mode, cfg)

    from explainshell import manpage as _manpage

    total = len(gz_files)
    counter = {"n": 0}

    def on_start(gz_path: str) -> None:
        counter["n"] += 1
        short_path = config.source_from_path(gz_path)
        logger.info(
            "[%d/%d] [%s] extracting (%s)...", counter["n"], total, short_path, mode
        )

    def on_result(gz_path: str, entry: ExtractionResult) -> None:
        short_path = config.source_from_path(gz_path)
        if entry.outcome == ExtractionOutcome.SKIPPED:
            logger.info("[%s] skipped: %s", short_path, entry.error)
            return
        if entry.outcome == ExtractionOutcome.FAILED:
            logger.error("failed to process %s: %s", short_path, entry.error)
            return
        name = _manpage.extract_name(gz_path)
        logger.info("=== %s ===", short_path)
        try:
            results = s.find_man_page(name)
            stored_mp = results[0]
        except errors.ProgramDoesNotExist:
            logger.info("  (not in DB, nothing to diff)")
        else:
            for line in format_diff(stored_mp, entry.mp):
                logger.info(line)

    return run_sequential(ext, gz_files, on_start=on_start, on_result=on_result)


def _run_dry_run(
    gz_files: list[str],
    mode: str,
    model: str | None,
    debug_dir: str | None,
) -> BatchResult:
    """Run --dry-run mode: extract but don't write to DB."""
    cfg = ExtractorConfig(model=model, debug_dir=debug_dir, fail_dir=debug_dir)
    ext = make_extractor(mode, cfg)

    def on_result(gz_path: str, entry: ExtractionResult) -> None:
        short_path = config.source_from_path(gz_path)
        if entry.outcome == ExtractionOutcome.SKIPPED:
            logger.info("[%s] skipped: %s", short_path, entry.error)
            return
        if entry.outcome == ExtractionOutcome.FAILED:
            logger.error("failed to process %s: %s", short_path, entry.error)
            return
        mp = entry.mp
        file_elapsed = _fmt_elapsed(entry.stats.elapsed_seconds)
        logger.info(
            "=== %s (%d option(s), %s) ===",
            short_path,
            len(mp.options),
            file_elapsed,
        )
        logger.info("  name: %s", mp.name)
        logger.info("  synopsis: %s", mp.synopsis)
        logger.info("  aliases: %s", mp.aliases)
        logger.info("  nested_cmd: %s", mp.nested_cmd)
        logger.info("  has_subcommands: %s", mp.has_subcommands)
        logger.info("  dashless_opts: %s", mp.dashless_opts)
        logger.info("  extractor: %s", mp.extractor)
        logger.info("  extraction_meta: %s", mp.extraction_meta)
        logger.info("")
        for i, opt in enumerate(mp.options):
            if i > 0:
                logger.info("")
            logger.info("  [%d]", i)
            logger.info("      short: %s", opt.short)
            logger.info("      long: %s", opt.long)
            logger.info("      has_argument: %s", opt.has_argument)
            if opt.positional:
                logger.info("      positional: %s", opt.positional)
            if opt.nested_cmd:
                logger.info("      nested_cmd: %s", opt.nested_cmd)
            desc = opt.text.strip()
            for line in desc.split("\n"):
                logger.info("      %s", line)

    return run_sequential(ext, gz_files, on_result=on_result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log.upper()),
        stream=sys.stdout,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.jobs < 1:
        print("error: --jobs must be >= 1", file=sys.stderr)
        return 1

    try:
        mode, model = _parse_mode(args.mode)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        diff_kind, diff_left, diff_right = _parse_diff(args.diff)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    is_extractor_diff = diff_kind == "extractors"

    if is_extractor_diff and mode:
        print("error: --mode is not allowed when using --diff A..B", file=sys.stderr)
        return 1

    if is_extractor_diff and args.dry_run:
        print("error: --dry-run is not allowed when using --diff A..B", file=sys.stderr)
        return 1

    if not is_extractor_diff and args.diff is not None and not mode:
        print("error: --mode is required when using --diff db", file=sys.stderr)
        return 1

    if not is_extractor_diff and not args.diff and not mode:
        print("error: --mode is required", file=sys.stderr)
        return 1

    if args.drop and args.dry_run:
        print("error: --drop and --dry-run are mutually exclusive", file=sys.stderr)
        return 1

    if args.drop and args.diff is not None:
        print("error: --drop and --diff are mutually exclusive", file=sys.stderr)
        return 1

    if args.overwrite and args.dry_run:
        print(
            "error: --overwrite and --dry-run are mutually exclusive", file=sys.stderr
        )
        return 1

    if args.overwrite and args.diff is not None:
        print("error: --overwrite and --diff are mutually exclusive", file=sys.stderr)
        return 1

    if args.batch is not None:
        if args.batch < 1:
            print("error: --batch must be >= 1", file=sys.stderr)
            return 1
        if not model:
            print(
                "error: --batch requires a model in --mode (e.g. llm:gemini/<model> or llm:openai/<model>)",
                file=sys.stderr,
            )
            return 1
        if not model.startswith(("gemini/", "openai/")):
            print(
                "error: --batch only supports gemini/ and openai/ models",
                file=sys.stderr,
            )
            return 1
        if mode != "llm":
            print("error: --batch only works with --mode llm:<model>", file=sys.stderr)
            return 1
        if args.diff is not None:
            print("error: --batch and --diff are mutually exclusive", file=sys.stderr)
            return 1

    db_path = args.db

    if args.drop and not args.dry_run:
        answer = input("Really drop all data? (y/n) ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    gz_files = _collect_gz_files(args.files)
    if not gz_files:
        print("No .gz files found.", file=sys.stderr)
        return 1

    s = store.Store.create(db_path) if not args.dry_run or diff_kind == "db" else None
    if s and args.drop:
        s.drop(confirm=True)

    t0 = time.monotonic()
    prefilter_skipped = 0

    from explainshell import manpage as _manpage

    if is_extractor_diff:
        batch_result = _run_diff_extractors(
            gz_files, diff_left, diff_right, args.debug_dir
        )
    elif diff_kind == "db":
        batch_result = _run_diff_db(
            gz_files, mode, model, args.debug_dir, args.dry_run, s
        )
    elif args.dry_run:
        batch_result = _run_dry_run(gz_files, mode, model, args.debug_dir)
    else:
        # Normal extraction: use runners.
        debug_dir = args.debug_dir if args.dry_run else None
        cfg = ExtractorConfig(model=model, debug_dir=debug_dir, fail_dir=args.debug_dir)
        extractor = make_extractor(mode, cfg)

        # Pre-filter already-stored files.
        work_files: list[str] = []
        for gz_path in gz_files:
            short_path = config.source_from_path(gz_path)
            name = _manpage.extract_name(gz_path)
            if s and not args.overwrite and _already_stored(s, short_path, name):
                logger.info("skipping %s (already stored)", short_path)
                prefilter_skipped += 1
            else:
                work_files.append(gz_path)

        total = len(gz_files)
        file_counter = {"n": 0}
        counter_lock = threading.Lock()

        def on_start(gz_path: str) -> None:
            with counter_lock:
                file_counter["n"] += 1
                n = file_counter["n"]
            short_path = config.source_from_path(gz_path)
            progress = f"[{n + prefilter_skipped}/{total}]"
            logger.info("%s [%s] extracting...", progress, short_path)

        def on_result(gz_path: str, entry: ExtractionResult) -> None:
            if entry.outcome == ExtractionOutcome.SUCCESS:
                if s:
                    s.add_manpage(entry.mp, entry.raw)
                short_path = config.source_from_path(gz_path)
                logger.info(
                    "[%s] done: %d option(s)",
                    short_path,
                    len(entry.mp.options),
                )
            elif entry.outcome == ExtractionOutcome.SKIPPED:
                short_path = config.source_from_path(gz_path)
                logger.info(
                    "[%s] skipped: %s",
                    short_path,
                    entry.error or "unknown reason",
                )

        if args.batch is not None:
            batch_result = run_batch(
                extractor,
                work_files,
                batch_size=args.batch,
                on_start=on_start,
                on_result=on_result,
            )
        elif args.jobs > 1:
            batch_result = run_parallel(
                extractor,
                work_files,
                args.jobs,
                on_start=on_start,
                on_result=on_result,
            )
        else:
            batch_result = run_sequential(
                extractor,
                work_files,
                on_start=on_start,
                on_result=on_result,
            )

    added = len(batch_result.succeeded)
    skipped = len(batch_result.skipped) + prefilter_skipped
    failed = len(batch_result.failed)

    # Update multi-cmd mappings (only when writing to DB).
    if s and added > 0 and not args.dry_run and not args.diff:
        s.update_subcommand_mappings()

    elapsed = time.monotonic() - t0
    dry_run_note = " (dry run)" if args.dry_run else ""
    token_note = ""
    if batch_result.stats.input_tokens:
        parts = [
            f"{_fmt_tokens(batch_result.stats.input_tokens)} in",
            f"{_fmt_tokens(batch_result.stats.output_tokens)} out",
        ]
        if batch_result.stats.reasoning_tokens:
            parts.append(
                f"{_fmt_tokens(batch_result.stats.reasoning_tokens)} reasoning"
            )
        token_note = f" Tokens: {' / '.join(parts)}."
    logger.info(
        "Done%s: %d extracted, %d skipped, %d failed.%s Total time: %s",
        dry_run_note,
        added,
        skipped,
        failed,
        token_note,
        _fmt_elapsed(elapsed),
    )
    return 0 if failed == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract man page options and store the results."
    )
    parser.add_argument(
        "--mode",
        help="Extraction mode: 'source', 'mandoc', 'llm:<model>', or 'hybrid:<model>'. Required unless --diff A..B.",
    )
    parser.add_argument("--db", default=config.DB_PATH, help="SQLite DB path")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Re-process pages already in the store",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        default=False,
        help="Drop all data before processing (prompts for confirmation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run LLM extraction but do not write results to the DB",
    )
    parser.add_argument(
        "--diff",
        nargs="?",
        const="db",
        help="Diff mode: 'db' (default) compares fresh extraction against the DB; "
        "'A..B' compares two extractors (e.g. source..mandoc, source..llm:gpt-4o)",
    )
    parser.add_argument(
        "--debug-dir",
        default="debug-output",
        help="Directory for debug files in dry-run mode (default: debug-output)",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel workers (default: 1)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Batch size for provider batch API (works with gemini/ and openai/ models)",
    )
    parser.add_argument(
        "--log",
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument("files", nargs="*", help=".gz files or directories")
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(main(args))
