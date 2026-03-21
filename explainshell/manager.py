"""
CLI entry point for man page extraction.

Usage:
    python -m explainshell.manager <command> [options]

Commands:
    extract --mode <mode> files...       Extract options from manpages and store in DB
    diff db --mode <mode> files...       Diff fresh extraction against the database
    diff extractors <A..B> files...      Compare two extractors head-to-head

Extraction modes (--mode):
    source              Use the roff parser
    mandoc              Use mandoc -T tree parser
    llm:<model>         Use an LLM (e.g. llm:openai/gpt-5-mini)
    hybrid:<model>      Try tree parser first, fall back to LLM when confidence is low
"""

import logging
import sys
import threading
import time

import click

from explainshell import config, errors, store, util
from explainshell.diff import format_diff
from explainshell.extraction import (
    BatchResult,
    ExtractorConfig,
    ExtractionOutcome,
    ExtractionResult,
    make_extractor,
)
from explainshell.extraction.runner import run, run_sequential

logger = logging.getLogger("explainshell.manager")

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


def _already_stored(s: store.Store, short_path: str, name: str) -> bool:
    try:
        results = s.find_man_page(name)
        return any(mp.source == short_path for mp in results)
    except errors.ProgramDoesNotExist:
        return False


def _parse_mode(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a mode value into (mode, model).

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
            raise ValueError("llm:<model> requires a model name (e.g. llm:gpt-5-mini)")
        return "llm", model
    if raw.startswith("hybrid:"):
        model = raw[7:]
        if not model:
            raise ValueError(
                "hybrid:<model> requires a model name (e.g. hybrid:gpt-5-mini)"
            )
        return "hybrid", model
    raise ValueError(
        f"invalid mode value: {raw!r} "
        f"(expected 'source', 'mandoc', 'llm:<model>', or 'hybrid:<model>')"
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
    left_label = left_mode if not left_model else f"{left_mode} ({left_model})"
    right_label = right_mode if not right_model else f"{right_mode} ({right_model})"
    label = f"{left_label} vs {right_label}"

    left_cfg = ExtractorConfig(model=left_model, fail_dir=debug_dir)
    right_cfg = ExtractorConfig(model=right_model, fail_dir=debug_dir)
    left_ext = make_extractor(left_mode, left_cfg)
    right_ext = make_extractor(right_mode, right_cfg)

    left_files: list[ExtractionResult] = []
    right_files: list[ExtractionResult] = []

    logger.info("running %s extractor on %d file(s)...", left_label, len(gz_files))
    run_sequential(left_ext, gz_files, on_result=lambda _p, e: left_files.append(e))
    logger.info("running %s extractor on %d file(s)...", right_label, len(gz_files))
    run_sequential(right_ext, gz_files, on_result=lambda _p, e: right_files.append(e))

    batch = BatchResult()
    for left_entry, right_entry in zip(left_files, right_files):
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
                    left_label,
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
                batch.n_failed += 1
            else:
                batch.n_skipped += 1
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
                left_label,
                util.fmt_tokens(li),
                util.fmt_tokens(lo),
            )
            logger.info(
                "    %s: %s in / %s out",
                right_label,
                util.fmt_tokens(ri),
                util.fmt_tokens(ro),
            )

        batch.n_succeeded += 1

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
            # Prefer exact source match (fully populated) over name lookup.
            try:
                results = s.find_man_page(short_path)
            except errors.ProgramDoesNotExist:
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
# Summary helper
# ---------------------------------------------------------------------------


def _log_summary(
    batch_result: BatchResult,
    prefilter_skipped: int,
    elapsed: float,
    dry_run: bool = False,
) -> int:
    """Log a summary of batch results and return the exit code."""
    added = batch_result.n_succeeded
    skipped = batch_result.n_skipped + prefilter_skipped
    failed = batch_result.n_failed

    dry_run_note = " (dry run)" if dry_run else ""
    token_note = ""
    if batch_result.stats.input_tokens:
        parts = [
            f"{util.fmt_tokens(batch_result.stats.input_tokens)} in",
            f"{util.fmt_tokens(batch_result.stats.output_tokens)} out",
        ]
        if batch_result.stats.reasoning_tokens:
            parts.append(
                f"{util.fmt_tokens(batch_result.stats.reasoning_tokens)} reasoning"
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


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging(log_level_str: str) -> None:
    """Configure logging for the CLI."""
    log_level = getattr(logging, log_level_str.upper())
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("explainshell").setLevel(log_level)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.option("--db", default=config.DB_PATH, help="SQLite DB path.")
@click.option("--log", "log_level", default="DEBUG", help="Log level (default: DEBUG).")
@click.pass_context
def cli(ctx: click.Context, db: str, log_level: str) -> None:
    """Manage the explainshell manpage database."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["log_level"] = log_level
    _setup_logging(log_level)


@cli.command()
@click.option(
    "-m",
    "--mode",
    required=True,
    help="Extraction strategy: source, mandoc, llm:<model>, or hybrid:<model>.",
)
@click.option("--dry-run", is_flag=True, help="Extract but don't write to DB.")
@click.option(
    "--overwrite", is_flag=True, help="Re-process pages already in the store."
)
@click.option(
    "--drop",
    is_flag=True,
    help="Drop all data before processing (prompts for confirmation).",
)
@click.option(
    "-j", "--jobs", type=int, default=1, help="Number of parallel workers (default: 1)."
)
@click.option(
    "--batch",
    type=int,
    default=None,
    help="Batch size for provider batch API (gemini/ and openai/ models).",
)
@click.option(
    "--debug-dir",
    default="debug-output",
    help="Directory for debug files (default: debug-output).",
)
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def extract(
    ctx: click.Context,
    mode: str,
    files: tuple[str, ...],
    dry_run: bool,
    overwrite: bool,
    drop: bool,
    jobs: int,
    batch: int | None,
    debug_dir: str,
) -> None:
    """Extract options from manpages and store in DB."""
    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    if jobs < 1:
        raise click.UsageError("--jobs must be >= 1")
    if drop and dry_run:
        raise click.UsageError("--drop and --dry-run are mutually exclusive")
    if overwrite and dry_run:
        raise click.UsageError("--overwrite and --dry-run are mutually exclusive")
    if batch is not None:
        if batch < 1:
            raise click.UsageError("--batch must be >= 1")
        if not model:
            raise click.UsageError(
                "--batch requires a model (e.g. llm:gemini/<model> or llm:openai/<model>)"
            )
        if not model.startswith(("gemini/", "openai/")):
            raise click.UsageError("--batch only supports gemini/ and openai/ models")
        if parsed_mode != "llm":
            raise click.UsageError("--batch only works with llm:<model> mode")

    db_path = ctx.obj["db"]
    gz_files = util.collect_gz_files(list(files))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    if drop:
        answer = input("Really drop all data? (y/n) ").strip().lower()
        if answer != "y":
            click.echo("Aborted.")
            return

    if dry_run:
        t0 = time.monotonic()
        batch_result = _run_dry_run(gz_files, parsed_mode, model, debug_dir)
        elapsed = time.monotonic() - t0
        rc = _log_summary(batch_result, 0, elapsed, dry_run=True)
        if rc != 0:
            sys.exit(rc)
        return

    s = store.Store.create(db_path)
    if drop:
        s.drop(confirm=True)

    t0 = time.monotonic()
    prefilter_skipped = 0

    from explainshell import manpage as _manpage

    cfg = ExtractorConfig(model=model, fail_dir=debug_dir)
    extractor = make_extractor(parsed_mode, cfg)

    # Pre-filter already-stored files.
    work_files: list[str] = []
    for gz_path in gz_files:
        short_path = config.source_from_path(gz_path)
        name = _manpage.extract_name(gz_path)
        if not overwrite and _already_stored(s, short_path, name):
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

    batch_result = run(
        extractor,
        work_files,
        batch_size=batch,
        jobs=jobs,
        on_start=on_start,
        on_result=on_result,
    )

    added = batch_result.n_succeeded
    if added > 0:
        s.update_subcommand_mappings()

    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, prefilter_skipped, elapsed)
    if rc != 0:
        sys.exit(rc)


# ---------------------------------------------------------------------------
# diff command group
# ---------------------------------------------------------------------------


@cli.group()
def diff() -> None:
    """Compare extraction results."""


@diff.command("db")
@click.option(
    "-m",
    "--mode",
    required=True,
    help="Extraction strategy: source, mandoc, llm:<model>, or hybrid:<model>.",
)
@click.option("--dry-run", is_flag=True, help="Enable extractor debug output.")
@click.option(
    "--debug-dir",
    default="debug-output",
    help="Directory for debug files (default: debug-output).",
)
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def diff_db_cmd(
    ctx: click.Context,
    mode: str,
    files: tuple[str, ...],
    dry_run: bool,
    debug_dir: str,
) -> None:
    """Diff fresh extraction against the database."""
    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    db_path = ctx.obj["db"]
    gz_files = util.collect_gz_files(list(files))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    s = store.Store.create(db_path)
    t0 = time.monotonic()
    batch_result = _run_diff_db(gz_files, parsed_mode, model, debug_dir, dry_run, s)
    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, 0, elapsed)
    if rc != 0:
        sys.exit(rc)


@diff.command("extractors")
@click.argument("spec")
@click.argument("files", nargs=-1, required=True)
@click.option(
    "--debug-dir",
    default="debug-output",
    help="Directory for debug files (default: debug-output).",
)
def diff_extractors_cmd(
    spec: str,
    files: tuple[str, ...],
    debug_dir: str,
) -> None:
    """Compare two extractors head-to-head.

    SPEC is A..B format (e.g. source..mandoc, source..llm:openai/gpt-5-mini).
    """
    if ".." not in spec:
        raise click.UsageError(
            f"invalid spec: {spec!r} (expected A..B, e.g. source..mandoc)"
        )
    parts = spec.split("..", 1)
    try:
        left = _parse_mode(parts[0])
        right = _parse_mode(parts[1])
    except ValueError as e:
        raise click.UsageError(str(e))

    gz_files = util.collect_gz_files(list(files))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    t0 = time.monotonic()
    batch_result = _run_diff_extractors(gz_files, left, right, debug_dir)
    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, 0, elapsed)
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    cli()
