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

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explainshell.extraction.llm.extractor import BatchExtractor
    from explainshell.extraction.salvage import BatchLogInfo

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
from explainshell.extraction.runner import (
    run,
    run_sequential,
    group_work_items,
    WorkItem,
)

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
        logger.info("  subcommands: %s", mp.subcommands)
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
# Symlink mapping helper
# ---------------------------------------------------------------------------


def _add_symlink_mapping(
    s: store.Store,
    gz_path: str,
    symlink_source: str,
    canonical_source: str,
) -> bool:
    """Insert or upgrade a mapping from a symlink's command name to the canonical source.

    If a mapping already exists with a lower score (e.g. lexgrog alias at score 1),
    upgrades it to score 10 since the symlink name is the primary command name.

    Returns True if a mapping was inserted or upgraded, False if unchanged.
    """
    from explainshell import manpage

    symlink_name = manpage.extract_name(gz_path)
    existing_score = s.mapping_score(symlink_name, canonical_source)
    if existing_score is not None and existing_score >= 10:
        logger.debug(
            "symlink mapping %s -> %s already exists (score %d)",
            symlink_name,
            canonical_source,
            existing_score,
        )
        return False
    if existing_score is not None:
        # Upgrade score from lower value (e.g. lexgrog alias at score 1).
        s.update_mapping_score(symlink_name, canonical_source, score=10)
        logger.info(
            "upgraded symlink mapping %s -> %s score %d -> 10",
            symlink_source,
            canonical_source,
            existing_score,
        )
    else:
        s.add_mapping(symlink_name, canonical_source, score=10)
        logger.info(
            "mapped symlink %s -> %s (name: %s)",
            symlink_source,
            canonical_source,
            symlink_name,
        )
    return True


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


def _log_summary(
    batch_result: BatchResult,
    prefilter_skipped: int,
    elapsed: float,
    dry_run: bool = False,
    symlinks_mapped: int = 0,
) -> int:
    """Log a summary of batch results and return the exit code."""
    added = batch_result.n_succeeded + symlinks_mapped
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
def cli(ctx: click.Context, db: str | None, log_level: str) -> None:
    """Manage the explainshell manpage database."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["log_level"] = log_level
    _setup_logging(log_level)


def _require_db(ctx: click.Context, *, must_exist: bool = False) -> str:
    """Return the --db path or raise a UsageError if not set.

    When *must_exist* is True, also verify the file is present on disk.
    """
    db = ctx.obj["db"]
    if not db:
        raise click.UsageError("No database path. Set DB_PATH or pass --db.")
    if must_exist and not os.path.isfile(db):
        raise click.UsageError(f"Database not found: {db}")
    return db


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

    db_path = _require_db(ctx)
    s = store.Store.create(db_path)
    if drop:
        s.drop(confirm=True)

    t0 = time.monotonic()
    prefilter_skipped = 0
    symlinks_mapped = 0
    symlink_files: list[tuple[str, str, str]] = []  # (gz_path, source, canonical)

    cfg = ExtractorConfig(model=model, fail_dir=debug_dir)
    extractor = make_extractor(parsed_mode, cfg)

    # Pre-filter already-stored files and separate symlinks.
    work_files: list[str] = []
    for gz_path in gz_files:
        short_path = config.source_from_path(gz_path)
        if not overwrite and s.has_manpage_source(short_path):
            logger.info("skipping %s (already stored)", short_path)
            prefilter_skipped += 1
            continue

        if os.path.islink(gz_path):
            canonical_path = os.path.realpath(gz_path)
            canonical_source = config.source_from_path(canonical_path)
            if canonical_source != short_path:
                symlink_files.append((gz_path, short_path, canonical_source))
                continue

        work_files.append(gz_path)

    extract_total = len(work_files) + prefilter_skipped
    file_counter = {"n": 0}
    counter_lock = threading.Lock()

    def on_start(gz_path: str) -> None:
        with counter_lock:
            file_counter["n"] += 1
            n = file_counter["n"]
        short_path = config.source_from_path(gz_path)
        progress = f"[{n + prefilter_skipped}/{extract_total}]"
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

    # Map symlinks to their canonical manpages (now that extraction is done).
    # Note: has_manpage_source checks the DB, not extraction outcomes. If a
    # canonical existed from a prior run and the current --overwrite attempt
    # failed, the old data is still valid and the symlink mapping is correct.
    # The extraction failure is reported separately in the summary.
    for gz_path, symlink_source, canonical_source in symlink_files:
        if s.has_manpage_source(canonical_source):
            if _add_symlink_mapping(s, gz_path, symlink_source, canonical_source):
                symlinks_mapped += 1
        else:
            logger.warning(
                "symlink %s -> %s: canonical not in DB, skipping",
                symlink_source,
                canonical_source,
            )

    added = batch_result.n_succeeded
    if added > 0 or symlinks_mapped > 0:
        s.update_subcommand_mappings()

    elapsed = time.monotonic() - t0
    rc = _log_summary(
        batch_result, prefilter_skipped, elapsed, symlinks_mapped=symlinks_mapped
    )
    if rc != 0:
        sys.exit(rc)


# ---------------------------------------------------------------------------
# salvage command
# ---------------------------------------------------------------------------


def _run_salvage(
    extractor: BatchExtractor,
    work_files: list[str],
    batch_size: int,
    log_info: BatchLogInfo,
    s: store.Store | None,
    dry_run: bool,
) -> BatchResult:
    """Salvage partial results from failed batches.

    Re-prepares files to reconstruct the batch grouping from the original run,
    then retrieves and finalizes results for failed batches only.
    """
    from explainshell.errors import SkippedExtraction
    from explainshell.extraction.salvage import salvageable_batches

    bp = extractor.batch_provider
    targets = salvageable_batches(log_info)
    if not targets:
        logger.info("no salvageable batches found in log")
        return BatchResult()

    logger.info(
        "found %d salvageable batch(es): %s",
        len(targets),
        ", ".join(f"{idx}" for idx in sorted(targets)),
    )

    # Phase 1: re-prepare all files to reconstruct the identical batch grouping.
    work_items: list[WorkItem] = []
    skipped_paths: list[str] = []
    for gz_path in work_files:
        try:
            prepared = extractor.prepare(gz_path)
        except SkippedExtraction:
            skipped_paths.append(gz_path)
            continue
        except Exception as e:
            logger.error("failed to prepare %s: %s", gz_path, e)
            skipped_paths.append(gz_path)
            continue
        work_items.append(WorkItem(gz_path, prepared))

    if not work_items:
        logger.warning("no files could be prepared")
        return BatchResult()

    batches = group_work_items(work_items, batch_size)
    total_batches = len(batches)
    del work_items

    if total_batches != log_info.total_batches:
        logger.error(
            "batch count mismatch: reconstructed %d batches but log says %d. "
            "Make sure you pass the same files and --batch-size as the original run.",
            total_batches,
            log_info.total_batches,
        )
        return BatchResult()

    logger.info(
        "reconstructed %d batch(es), salvaging %d failed batch(es)...",
        total_batches,
        len(targets),
    )

    # Phase 2: retrieve and finalize failed batches.
    result = BatchResult()
    for batch_idx, batch_id in sorted(targets.items()):
        batch_items = batches[batch_idx - 1]  # 1-based → 0-based
        logger.info(
            "salvaging batch %d/%d (%s, %d file(s))...",
            batch_idx,
            total_batches,
            batch_id,
            len(batch_items),
        )

        try:
            job = bp.retrieve_batch(batch_id)
        except Exception as e:
            logger.error("failed to retrieve batch %s: %s", batch_id, e)
            result.n_failed += len(batch_items)
            continue

        try:
            collected = bp.collect_results(job)
        except Exception as e:
            logger.error("failed to collect results for batch %s: %s", batch_id, e)
            result.n_failed += len(batch_items)
            continue

        result.stats.input_tokens += collected.usage.input_tokens
        result.stats.output_tokens += collected.usage.output_tokens
        result.stats.reasoning_tokens += collected.usage.reasoning_tokens

        logger.info(
            "batch %d: collected %d result(s) from provider",
            batch_idx,
            len(collected.responses),
        )

        for item_idx, (gz_path, prepared) in enumerate(batch_items):
            short_path = config.source_from_path(gz_path)

            # Skip files already in DB (from a prior successful run or partial salvage).
            if not dry_run and s.has_manpage_source(short_path):
                logger.info("[%s] already in DB, skipping", short_path)
                result.n_skipped += 1
                continue

            n_chunks = prepared.n_chunks
            responses: list[str] = []
            file_failed = False

            for chunk_idx in range(n_chunks):
                key_str = f"{item_idx}:{chunk_idx}"
                response_text = collected.responses.get(key_str)
                if response_text is None:
                    logger.warning(
                        "[%s] missing result for chunk %d (key %s)",
                        short_path,
                        chunk_idx,
                        key_str,
                    )
                    file_failed = True
                    break
                responses.append(response_text)

            if file_failed:
                result.n_failed += 1
                continue

            try:
                entry = extractor.finalize(gz_path, prepared, responses)
            except Exception as e:
                logger.error("[%s] failed to finalize: %s", short_path, e)
                result.n_failed += 1
                continue

            if not dry_run:
                s.add_manpage(entry.mp, entry.raw)
            logger.info(
                "[%s] salvaged: %d option(s)", short_path, len(entry.mp.options)
            )
            result.stats += entry.stats
            result.n_succeeded += 1

    return result


@cli.command()
@click.option(
    "-m",
    "--mode",
    required=True,
    help="Must match the original run's mode (e.g. llm:openai/gpt-5-mini).",
)
@click.option(
    "--batch-size",
    type=int,
    required=True,
    help="Must match the original run's --batch value.",
)
@click.option(
    "--log-file",
    required=True,
    type=click.Path(exists=True),
    help="Log file from the failed extract --batch run.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be salvaged without writing to DB."
)
@click.option(
    "--debug-dir",
    default=None,
    help="Directory for debug files.",
)
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def salvage(
    ctx: click.Context,
    mode: str,
    batch_size: int,
    log_file: str,
    dry_run: bool,
    debug_dir: str | None,
    files: tuple[str, ...],
) -> None:
    """Salvage partial results from failed batch extraction runs.

    Re-prepares the same input files to reconstruct the batch grouping,
    then retrieves and finalizes results for batches that failed during
    the original run (expired, connection errors, cancellation timeouts).

    The FILES argument must match exactly what was passed to the original
    extract command.
    """
    from explainshell.extraction.llm.extractor import BatchExtractor
    from explainshell.extraction.salvage import parse_batch_log

    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    if parsed_mode != "llm" or not model:
        raise click.UsageError("salvage only works with llm:<model> mode")
    if not model.startswith(("gemini/", "openai/")):
        raise click.UsageError("salvage only supports gemini/ and openai/ models")

    gz_files = util.collect_gz_files(list(files))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    log_info = parse_batch_log(log_file)
    if not log_info.submitted:
        raise click.UsageError("No submitted batches found in the log file.")
    if not log_info.failed:
        click.echo("No failed batches found in the log file. Nothing to salvage.")
        return

    if not dry_run:
        db_path = _require_db(ctx)
        s = store.Store.create(db_path)
    else:
        db_path = None
        s = None

    cfg = ExtractorConfig(model=model, debug_dir=debug_dir)
    extractor = make_extractor(parsed_mode, cfg)
    if not isinstance(extractor, BatchExtractor):
        raise click.UsageError("extractor does not support batch mode")

    # Replicate the same pre-filtering as the original extract command:
    # skip symlinks and files that were "already stored" at the time of the
    # original run (parsed from the log).  This is necessary to reconstruct
    # the identical batch grouping.
    work_files: list[str] = []
    for gz_path in gz_files:
        if os.path.islink(gz_path):
            continue
        short_path = config.source_from_path(gz_path)
        if short_path in log_info.already_stored:
            continue
        work_files.append(gz_path)

    t0 = time.monotonic()
    batch_result = _run_salvage(extractor, work_files, batch_size, log_info, s, dry_run)

    if not dry_run and s is not None and batch_result.n_succeeded > 0:
        s.update_subcommand_mappings()

    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, 0, elapsed, dry_run=dry_run)
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

    db_path = _require_db(ctx)
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


# ---------------------------------------------------------------------------
# show command group
# ---------------------------------------------------------------------------


@cli.group()
def show() -> None:
    """Query the manpage database."""


@show.command("manpage")
@click.argument("name")
@click.option("--raw", is_flag=True, help="Also print raw manpage text.")
@click.pass_context
def show_manpage(ctx: click.Context, name: str, raw: bool) -> None:
    """Look up a command and display its extracted options."""
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    try:
        results = s.find_man_page(name)
    except errors.ProgramDoesNotExist:
        click.echo(f"Not found: {name}", err=True)
        sys.exit(1)
    mp = results[0]
    click.echo(f"source: {mp.source}")
    click.echo(f"name: {mp.name}")
    click.echo(f"synopsis: {mp.synopsis}")
    click.echo(f"aliases: {mp.aliases}")
    click.echo(f"nested_cmd: {mp.nested_cmd}")
    click.echo(f"subcommands: {mp.subcommands}")
    click.echo(f"dashless_opts: {mp.dashless_opts}")
    click.echo(f"extractor: {mp.extractor}")
    click.echo(f"options: {len(mp.options)}")
    click.echo("")
    for i, opt in enumerate(mp.options):
        if i > 0:
            click.echo("")
        click.echo(f"  [{i}]")
        click.echo(f"      short: {opt.short}")
        click.echo(f"      long: {opt.long}")
        click.echo(f"      has_argument: {opt.has_argument}")
        if opt.positional:
            click.echo(f"      positional: {opt.positional}")
        if opt.nested_cmd:
            click.echo(f"      nested_cmd: {opt.nested_cmd}")
        desc = opt.text.strip()
        for line in desc.split("\n"):
            click.echo(f"      {line}")

    if raw:
        raw_mp = s.get_raw_manpage(mp.source)
        if raw_mp:
            click.echo("")
            click.echo("--- raw manpage ---")
            click.echo(raw_mp.source_text)
        else:
            click.echo("")
            click.echo("(no raw manpage stored)")

    if len(results) > 1:
        click.echo("")
        click.echo("also available:")
        for alt in results[1:]:
            click.echo(f"  {alt.source} ({alt.name})")


@show.command("distros")
@click.pass_context
def show_distros(ctx: click.Context) -> None:
    """List available distributions."""
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    for distro, release in s.distros():
        click.echo(f"{distro}/{release}")


@show.command("sections")
@click.argument("distro")
@click.argument("release")
@click.pass_context
def show_sections(ctx: click.Context, distro: str, release: str) -> None:
    """List sections for a distro/release."""
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    for section in s.list_sections(distro, release):
        click.echo(section)


@show.command("manpages")
@click.argument("prefix")
@click.pass_context
def show_manpages(ctx: click.Context, prefix: str) -> None:
    """List manpages matching a source prefix."""
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    for source in s.list_manpages(prefix):
        click.echo(source)


@show.command("mappings")
@click.option("--prefix", default=None, help="Filter by source prefix.")
@click.pass_context
def show_mappings(ctx: click.Context, prefix: str | None) -> None:
    """List command->manpage mappings."""
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    for src, dst in s.mappings():
        if prefix is None or dst.startswith(prefix):
            click.echo(f"{src} -> {dst}")


@show.command("stats")
@click.pass_context
def show_stats(ctx: click.Context) -> None:
    """Print aggregate database statistics."""
    import sqlite3

    db_path = _require_db(ctx, must_exist=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    n_manpages = conn.execute("SELECT COUNT(*) AS c FROM manpages").fetchone()["c"]
    n_parsed = conn.execute("SELECT COUNT(*) AS c FROM parsed_manpages").fetchone()["c"]
    n_mappings = conn.execute("SELECT COUNT(*) AS c FROM mappings").fetchone()["c"]
    click.echo(f"manpages (raw):    {n_manpages}")
    click.echo(f"parsed_manpages:   {n_parsed}")
    click.echo(f"mappings:          {n_mappings}")

    # Per-distro breakdown.
    rows = conn.execute("""
        SELECT
            SUBSTR(source, 1, INSTR(source, '/') - 1) as distro,
            SUBSTR(source, INSTR(source, '/') + 1,
                   INSTR(SUBSTR(source, INSTR(source, '/') + 1), '/') - 1) as release,
            COUNT(*) as cnt
        FROM parsed_manpages
        GROUP BY distro, release
        ORDER BY distro, release
    """).fetchall()
    if rows:
        click.echo("")
        click.echo("per distro/release:")
        for row in rows:
            click.echo(f"  {row['distro']}/{row['release']}: {row['cnt']}")

    conn.close()


# ---------------------------------------------------------------------------
# db-check command
# ---------------------------------------------------------------------------

_DB_CHECK_RED = "\033[31m"
_DB_CHECK_CYAN = "\033[36m"
_DB_CHECK_RESET = "\033[0m"


@cli.command("db-check")
@click.pass_context
def db_check_cmd(ctx: click.Context) -> None:
    """Run database integrity checks."""
    from explainshell.db_check import check as run_db_check

    issues = run_db_check(_require_db(ctx, must_exist=True))
    if not issues:
        click.echo("No issues found.")
        return

    n_errors = sum(1 for sev, _ in issues if sev == "error")
    n_warnings = sum(1 for sev, _ in issues if sev == "warning")
    for severity, msg in issues:
        label = (
            f"{_DB_CHECK_RED}ERROR{_DB_CHECK_RESET}"
            if severity == "error"
            else f"{_DB_CHECK_CYAN}WARNING{_DB_CHECK_RESET}"
        )
        click.echo(f"  {label}: {msg}")
    click.echo(f"\n{n_errors} error(s), {n_warnings} warning(s)")
    if n_errors:
        sys.exit(1)


if __name__ == "__main__":
    cli()
