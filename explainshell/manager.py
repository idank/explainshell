"""
CLI entry point for man page extraction.

Usage:
    python -m explainshell.manager <command> [options]

Commands:
    extract --mode <mode> files...       Extract options from manpages and store in DB
    diff db --mode <mode> files...       Diff fresh extraction against the database
    diff extractors <A..B> files...      Compare two extractors head-to-head

Extraction modes (--mode):
    llm:<model>         Use an LLM (e.g. llm:openai/gpt-5-mini, llm:codex/gpt-5.4-mini)

Reasoning effort can be appended to the model string:
    openai/<model>/<effort>     e.g. llm:openai/o3/medium (low, medium, high)
    azure/<model>/<effort>      e.g. llm:azure/o3/high
    gemini/<model>/<budget>     e.g. llm:gemini/gemini-2.5-flash/8192 (thinking token budget)
    codex/<model>/<effort>      e.g. llm:codex/o3/high
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

import click

from explainshell import config, errors, store, util
from explainshell.diff import format_diff
from explainshell.extraction import (
    BatchResult,
    ExtractionOutcome,
    ExtractionResult,
    ExtractorConfig,
    make_extractor,
    prefilter,
)
from explainshell.extraction.manifest import FileBatchManifestWriter
from explainshell.extraction.report import (
    DbCounts,
    ExtractConfig,
    ExtractionReport,
    ExtractSummary,
    FailureEntry,
    GitInfo,
    OptionCountSummary,
    SkipEntry,
    TokenUsage,
)
from explainshell.extraction.runner import run

logger = logging.getLogger("explainshell.manager")

_LOGS_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

# ANSI color helpers.
_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_BATCH_MODEL_PREFIXES = ("gemini/", "openai/", "azure/")

# gz size in bytes that splits the corpus into "cheap-model safe" (<=) and
# "needs capable model" (>). Derived from tools/experiments/eval_size_routing.py:
# pages at or below this threshold matched the capable model on ~98% of files;
# disagreement starts in the 4-8 KB bucket.
_SIZE_FILTER_THRESHOLD = 2048


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _parse_mode(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a mode value into (mode, model).

    Returns ("llm", "<model>").

    Raises ValueError on invalid input.
    """
    if raw is None:
        return None, None
    if raw.startswith("llm:"):
        model = raw[4:]
        if not model:
            raise ValueError("llm:<model> requires a model name (e.g. llm:gpt-5-mini)")
        return "llm", model
    raise ValueError(f"invalid mode value: {raw!r} (expected 'llm:<model>')")


# ---------------------------------------------------------------------------
# Diff mode helpers
# ---------------------------------------------------------------------------


def _run_diff_extractors(
    gz_files: list[str],
    diff_left: tuple,
    diff_right: tuple,
    run_dir: str,
    batch_size: int | None = None,
) -> BatchResult:
    """Run --diff A..B mode: compare two extractors on each file."""
    left_mode, left_model = diff_left
    right_mode, right_model = diff_right
    left_label = left_mode if not left_model else f"{left_mode} ({left_model})"
    right_label = right_mode if not right_model else f"{right_mode} ({right_model})"
    label = f"{left_label} vs {right_label}"

    left_cfg = ExtractorConfig(model=left_model, run_dir=run_dir)
    right_cfg = ExtractorConfig(model=right_model, run_dir=run_dir)
    left_ext = make_extractor(left_mode, left_cfg)
    right_ext = make_extractor(right_mode, right_cfg)

    left_files: list[ExtractionResult] = []
    right_files: list[ExtractionResult] = []

    left_bs = batch_size
    right_bs = batch_size

    logger.info("running %s extractor on %d file(s)...", left_label, len(gz_files))
    run(
        left_ext,
        gz_files,
        batch_size=left_bs,
        on_result=lambda _p, e: left_files.append(e),
    )
    logger.info("running %s extractor on %d file(s)...", right_label, len(gz_files))
    run(
        right_ext,
        gz_files,
        batch_size=right_bs,
        on_result=lambda _p, e: right_files.append(e),
    )

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
    run_dir: str,
    debug: bool,
    s: store.Store,
    batch_size: int | None = None,
) -> BatchResult:
    """Run --diff db mode: compare fresh extraction against the DB."""
    cfg = ExtractorConfig(model=model, run_dir=run_dir, debug=debug)
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

    return run(
        ext, gz_files, batch_size=batch_size, on_start=on_start, on_result=on_result
    )


def _format_decision(d: prefilter.Decision) -> str:
    """One-line summary of a Decision for --dry-run output."""
    if isinstance(d, prefilter.Work):
        return f"WORK         {d.short_path}"
    if isinstance(d, prefilter.SizeSkip):
        cmp = ">" if d.direction == "small" else "<="
        return f"SIZE-SKIP    {d.short_path} ({d.size} {cmp} {d.threshold})"
    if isinstance(d, prefilter.AlreadyStored):
        return f"ALREADY      {d.short_path}"
    if isinstance(d, prefilter.FilterSkip):
        return (
            f"FILTER-SKIP  {d.short_path} "
            f"(stored {d.stored_extractor}/{d.stored_model})"
        )
    if isinstance(d, prefilter.Symlink):
        loc = "in-input-set" if d.canonical_in_inputs else "not-in-inputs"
        stale = ", stale-in-db" if d.stale_in_db else ""
        return f"SYMLINK      {d.short_path} -> {d.canonical_source} ({loc}{stale})"
    if isinstance(d, prefilter.ContentDup):
        return f"CONTENT-DUP  {d.short_path} -> {d.canonical_source}"
    return repr(d)


def _run_plan(
    gz_files: list[str],
    s: store.Store,
    *,
    overwrite: bool,
    filter_specs: list[tuple[str, str | None]],
    small_only: bool,
    large_only: bool,
) -> None:
    """Print the prefilter classification for each file; no extraction, no writes."""
    classifier = prefilter.Classifier(
        s=s,
        overwrite=overwrite,
        filter_specs=filter_specs,
        small_only=small_only,
        large_only=large_only,
        size_threshold=_SIZE_FILTER_THRESHOLD,
        normalized_inputs={os.path.normpath(p) for p in gz_files},
    )
    counts: dict[str, int] = {}
    for gz_path in gz_files:
        d = classifier.classify(gz_path)
        kind = type(d).__name__
        counts[kind] = counts.get(kind, 0) + 1
        logger.info("%s", _format_decision(d))
    summary = ", ".join(f"{n} {k.lower()}" for k, n in sorted(counts.items()))
    logger.info("plan: %s (%d total)", summary or "(empty)", len(gz_files))


# ---------------------------------------------------------------------------
# Symlink mapping helper
# ---------------------------------------------------------------------------


def _add_alias_mapping(
    s: store.Store,
    gz_path: str,
    alias_source: str,
    canonical_source: str,
) -> bool:
    """Insert or upgrade a mapping from an alias command name to the canonical source.

    Used for both symlinks and content-identical files (e.g. cross-compiler
    variants).  If a mapping already exists with a lower score (e.g. lexgrog
    alias at score 1), upgrades it to score 10 since the alias name is the
    primary command name.

    Returns True if a mapping was inserted or upgraded, False if unchanged.
    """
    from explainshell import manpage

    alias_name = manpage.extract_name(gz_path)
    existing_score = s.mapping_score(alias_name, canonical_source)
    if existing_score is not None and existing_score >= 10:
        logger.debug(
            "alias mapping %s -> %s already exists (score %d)",
            alias_name,
            canonical_source,
            existing_score,
        )
        return False
    if existing_score is not None:
        # Upgrade score from lower value (e.g. lexgrog alias at score 1).
        s.update_mapping_score(alias_name, canonical_source, score=10)
        logger.info(
            "upgraded alias mapping %s -> %s score %d -> 10",
            alias_source,
            canonical_source,
            existing_score,
        )
    else:
        s.add_mapping(alias_name, canonical_source, score=10)
        logger.info(
            "mapped alias %s -> %s (name: %s)",
            alias_source,
            canonical_source,
            alias_name,
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
    content_deduped: int = 0,
) -> int:
    """Log a summary of batch results and return the exit code."""
    added = batch_result.n_succeeded + symlinks_mapped + content_deduped
    skipped = batch_result.n_skipped + prefilter_skipped
    failed = batch_result.n_failed

    status = "Interrupted" if batch_result.interrupted else "Done"
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
        "%s%s: %d extracted, %d skipped, %d failed.%s Total time: %s",
        status,
        dry_run_note,
        added,
        skipped,
        failed,
        token_note,
        _fmt_elapsed(elapsed),
    )
    return 0 if failed == 0 else 1


def _write_report(run_dir: str, report: ExtractionReport) -> None:
    """Write report.json to the run directory."""
    path = os.path.join(run_dir, "report.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(report.model_dump_json(indent=2, exclude_none=True))
        f.write("\n")
    os.replace(tmp, path)
    logger.info("report written to %s", path)
    # Clean up standalone batch manifest (now embedded in report).
    manifest_path = os.path.join(run_dir, "batch-manifest.json")
    if os.path.isfile(manifest_path):
        os.remove(manifest_path)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


_LOG_FMT = "%(asctime)s %(levelname)-5s [%(name)s:%(lineno)d] %(message)s"
_LOG_DATEFMT = "%H:%M:%S"


def _setup_console_logging(log_level_str: str) -> None:
    """Configure stderr logging only — no run dir, no file handler."""
    log_level = getattr(logging, log_level_str.upper())
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format=_LOG_FMT,
        datefmt=_LOG_DATEFMT,
    )
    logging.getLogger("explainshell").setLevel(log_level)


def _attach_run_log(log_level_str: str) -> str:
    """Create a timestamped run dir and attach a file handler. Returns the run dir."""
    import datetime

    log_level = getattr(logging, log_level_str.upper())
    # local wall-clock on purpose — these dir names are read by humans
    timestamp = datetime.datetime.now(tz=None).astimezone().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(_LOGS_ROOT, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "run.log")

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    logging.getLogger("explainshell").addHandler(file_handler)

    # Logged after the file handler is attached so the run log captures it.
    logger.info("command line: %s", " ".join(sys.argv))
    logger.info("logging to %s", log_path)
    return run_dir


def _ensure_run_dir(ctx: click.Context) -> str:
    """Lazily create the run dir for commands that need one. Cached on ctx."""
    run_dir = ctx.obj.get("run_dir")
    if run_dir is None:
        run_dir = _attach_run_log(ctx.obj["log_level"])
        ctx.obj["run_dir"] = run_dir
    return run_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.option("--db", default=config.DB_PATH, help="SQLite DB path.")
@click.option("--log", "log_level", default="INFO", help="Log level (default: INFO).")
@click.pass_context
def cli(ctx: click.Context, db: str | None, log_level: str) -> None:
    """Manage the explainshell manpage database."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["log_level"] = log_level
    ctx.obj["run_dir"] = None
    _setup_console_logging(log_level)


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
    help="Extraction strategy: llm:<model>.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the prefilter decision per file; don't extract or write to DB.",
)
@click.option(
    "--overwrite", is_flag=True, help="Re-process pages already in the store."
)
@click.option(
    "--filter-db",
    "filter_db",
    multiple=True,
    help=(
        "With --overwrite, only re-extract existing manpages whose DB row's "
        "extractor matches <spec>. Same syntax as --mode. Repeatable: pass "
        "multiple times to match any of several extractors/models."
    ),
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
    help="Batch size for provider batch API (gemini/, openai/, and azure/ models).",
)
@click.option(
    "--debug",
    "debug",
    is_flag=True,
    help="Write full prompt/response debug artifacts.",
)
@click.option(
    "--small-only",
    "small_only",
    is_flag=True,
    default=False,
    help="Process only files whose gz size is <= 2048 bytes (route to a cheap model).",
)
@click.option(
    "--large-only",
    "large_only",
    is_flag=True,
    default=False,
    help="Process only files whose gz size exceeds 2048 bytes (route to a capable model).",
)
@click.option(
    "--reason",
    default=None,
    help=(
        "Why this extraction is being run. Recorded in the db_events row so future "
        "reviewers can see what motivated bulk re-runs. Required for runs with "
        "more than 100 manpages."
    ),
)
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def extract(
    ctx: click.Context,
    mode: str,
    files: tuple[str, ...],
    dry_run: bool,
    overwrite: bool,
    filter_db: tuple[str, ...],
    drop: bool,
    jobs: int,
    batch: int | None,
    debug: bool,
    small_only: bool,
    large_only: bool,
    reason: str | None,
) -> None:
    """Extract options from manpages and store in DB."""
    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    if jobs < 1:
        raise click.UsageError("--jobs must be >= 1")
    if small_only and large_only:
        raise click.UsageError("--small-only and --large-only are mutually exclusive")
    if drop and dry_run:
        raise click.UsageError("--drop and --dry-run are mutually exclusive")

    filter_specs: list[tuple[str, str | None]] = []
    if filter_db:
        if not overwrite:
            raise click.UsageError("--filter-db requires --overwrite")
        for spec in filter_db:
            try:
                filter_specs.append(_parse_mode(spec))
            except ValueError as e:
                raise click.UsageError(f"--filter-db: {e}")

    if batch is not None:
        if batch < 1:
            raise click.UsageError("--batch must be >= 1")
        if not model:
            raise click.UsageError(
                "--batch requires a model (e.g. llm:gemini/<model>, llm:openai/<model>, or llm:azure/<deployment>)"
            )
        if not model.startswith(_BATCH_MODEL_PREFIXES):
            raise click.UsageError(
                "--batch only supports gemini/, openai/, and azure/ models"
            )

    try:
        gz_files = util.collect_gz_files(list(files))
    except ValueError as e:
        raise click.UsageError(str(e))
    if not gz_files:
        raise click.UsageError("No .gz files found.")
    if not dry_run and len(gz_files) > 100 and not reason:
        raise click.UsageError(
            f"--reason is required when extracting more than 100 manpages "
            f"(got {len(gz_files)})"
        )

    if drop:
        answer = input("Really drop all data? (y/n) ").strip().lower()
        if answer != "y":
            click.echo("Aborted.")
            return

    if dry_run:
        db_path = _require_db(ctx, must_exist=True)
        s = store.Store(db_path, read_only=True)
        _run_plan(
            gz_files,
            s,
            overwrite=overwrite,
            filter_specs=filter_specs,
            small_only=small_only,
            large_only=large_only,
        )
        return

    run_dir: str = _ensure_run_dir(ctx)
    db_path = _require_db(ctx)
    s = store.Store.create(db_path)
    if drop:
        s.drop(confirm=True)

    db_before = s.counts()
    t0 = time.monotonic()
    symlinks_mapped = 0
    content_deduped = 0

    cfg = ExtractorConfig(model=model, run_dir=run_dir, debug=debug)
    extractor = make_extractor(parsed_mode, cfg)

    # Classify inputs (size / symlink / filter-db / already-stored / content-dup)
    # before extraction. Use os.path.normpath (not realpath) so symlinks don't
    # resolve to their targets when checking whether a symlink's canonical is
    # also in the input set.
    classifier = prefilter.Classifier(
        s=s,
        overwrite=overwrite,
        filter_specs=filter_specs,
        small_only=small_only,
        large_only=large_only,
        size_threshold=_SIZE_FILTER_THRESHOLD,
        normalized_inputs={os.path.normpath(p) for p in gz_files},
    )
    decisions = [classifier.classify(p) for p in gz_files]
    classified = prefilter.apply_decisions(decisions, s, filter_db=filter_db)

    prefilter_skipped = classified.prefilter_skipped
    symlink_files = classified.symlinks
    content_dup_files = classified.content_dups
    work_files = classified.work_files

    if classified.size_filtered:
        which = "--small-only" if small_only else "--large-only"
        logger.info(
            "size-filtered %d file(s) by %s (threshold=%d)",
            classified.size_filtered,
            which,
            _SIZE_FILTER_THRESHOLD,
        )
    already_stored_skipped = prefilter_skipped - classified.size_filtered
    if already_stored_skipped:
        logger.info("skipped %d already stored file(s)", already_stored_skipped)
    if content_dup_files:
        logger.info("deduplicated %d content-identical file(s)", len(content_dup_files))

    extract_total = len(work_files) + prefilter_skipped

    start_counter = {"n": 0}
    result_counter = {"n": 0}
    counter_lock = threading.Lock()
    option_counts: list[int] = []
    failures: list[FailureEntry] = []
    skips: list[SkipEntry] = []

    def on_start(gz_path: str) -> None:
        with counter_lock:
            start_counter["n"] += 1
            n = start_counter["n"]
        short_path = config.source_from_path(gz_path)
        progress = f"[{n + prefilter_skipped}/{extract_total}]"
        logger.info("%s [%s] extracting...", progress, short_path)

    def on_result(gz_path: str, entry: ExtractionResult) -> None:
        with counter_lock:
            result_counter["n"] += 1
            n = result_counter["n"]
        short_path = config.source_from_path(gz_path)
        progress = f"[{n + prefilter_skipped}/{extract_total}]"
        reason_value = (
            entry.reason_class.value if entry.reason_class is not None else None
        )
        if entry.outcome == ExtractionOutcome.SUCCESS:
            s.add_manpage(entry.mp, entry.raw)
            option_counts.append(len(entry.mp.options))
            logger.info(
                "%s [%s] done: %d option(s)",
                progress,
                short_path,
                len(entry.mp.options),
            )
        elif entry.outcome == ExtractionOutcome.SKIPPED:
            skips.append(
                SkipEntry(
                    path=short_path,
                    reason_class=reason_value,
                    message=entry.error or "unknown reason",
                )
            )
            logger.info(
                "%s [%s] skipped: %s",
                progress,
                short_path,
                entry.error or "unknown reason",
            )
        elif entry.outcome == ExtractionOutcome.FAILED:
            failures.append(
                FailureEntry(
                    path=short_path,
                    reason_class=reason_value,
                    message=entry.error or "unknown error",
                )
            )
            logger.error(
                "%s [%s] FAILED: %s",
                progress,
                short_path,
                entry.error or "unknown error",
            )

    manifest = None
    if batch is not None:
        manifest_path = os.path.join(run_dir, "batch-manifest.json")
        manifest = FileBatchManifestWriter(manifest_path, model=model, batch_size=batch)

    fatal_error: str | None = None
    batch_result = BatchResult()
    try:
        batch_result = run(
            extractor,
            work_files,
            batch_size=batch,
            jobs=jobs,
            on_start=on_start,
            on_result=on_result,
            manifest=manifest,
        )

        # Map symlinks to their canonical manpages (now that extraction is done).
        # Note: has_manpage_source checks the DB, not extraction outcomes. If a
        # canonical existed from a prior run and the current --overwrite attempt
        # failed, the old data is still valid and the symlink mapping is correct.
        # The extraction failure is reported separately in the summary.
        for gz_path, symlink_source, canonical_source in symlink_files:
            if s.has_manpage_source(canonical_source):
                if _add_alias_mapping(s, gz_path, symlink_source, canonical_source):
                    symlinks_mapped += 1
            else:
                logger.warning(
                    "symlink %s -> %s: canonical not in DB, skipping",
                    symlink_source,
                    canonical_source,
                )

        # Map content-identical files (e.g. cross-compiler variants) the same way.
        for gz_path, dup_source, canonical_source in content_dup_files:
            if s.has_manpage_source(canonical_source):
                if _add_alias_mapping(s, gz_path, dup_source, canonical_source):
                    content_deduped += 1
            else:
                logger.debug(
                    "content-dup %s -> %s: canonical not in DB, skipping",
                    dup_source,
                    canonical_source,
                )

        added = batch_result.n_succeeded
        if added > 0 or symlinks_mapped > 0 or content_deduped > 0:
            s.update_subcommand_mappings()
    except KeyboardInterrupt:
        logger.info("interrupted by user (Ctrl+C)")
        batch_result.interrupted = True
    except errors.FatalExtractionError as e:
        logger.error("FATAL: %s", e)
        batch_result = BatchResult(n_failed=1)
        fatal_error = str(e)

    elapsed = time.monotonic() - t0
    rc = _log_summary(
        batch_result,
        prefilter_skipped,
        elapsed,
        symlinks_mapped=symlinks_mapped,
        content_deduped=content_deduped,
    )

    import datetime as _dt

    report = ExtractionReport(
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        git=GitInfo(**util.git_metadata()),
        config=ExtractConfig(
            mode=parsed_mode,
            model=model,
            overwrite=overwrite,
            filter_db=list(filter_db),
            drop=drop,
            jobs=jobs,
            batch_size=batch,
            debug=debug,
            small_only=small_only,
            large_only=large_only,
        ),
        elapsed_seconds=round(elapsed, 1),
        summary=ExtractSummary(
            succeeded=batch_result.n_succeeded,
            skipped=batch_result.n_skipped + prefilter_skipped,
            failed=batch_result.n_failed,
            prefilter_skipped=prefilter_skipped,
            symlinks_mapped=symlinks_mapped,
            content_deduped=content_deduped,
            interrupted=batch_result.interrupted,
            fatal_error=fatal_error,
        ),
        db_before=DbCounts(**db_before),
        db_after=DbCounts(**s.counts()),
        usage=TokenUsage(
            input_tokens=batch_result.stats.input_tokens,
            output_tokens=batch_result.stats.output_tokens,
            reasoning_tokens=batch_result.stats.reasoning_tokens,
            chunks=batch_result.stats.chunks,
            plain_text_chars=batch_result.stats.plain_text_len,
        ),
        option_counts=OptionCountSummary.from_counts(option_counts),
        failures=failures,
        skips=skips,
        batch_manifest=manifest.to_dict() if manifest is not None else None,
        reason=reason,
    )
    _write_report(run_dir, report)

    # Log the extraction event into the DB itself so the production service
    # can inspect when / how the database was last updated.
    s.log_event(
        "extraction",
        report.model_dump(exclude={"batch_manifest"}, exclude_none=True),
    )

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
    help="Extraction strategy: llm:<model>.",
)
@click.option(
    "--batch",
    type=int,
    default=None,
    help="Batch size for provider batch API (gemini/, openai/, and azure/ models).",
)
@click.option(
    "--debug",
    "debug",
    is_flag=True,
    help="Write full prompt/response debug artifacts.",
)
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def diff_db_cmd(
    ctx: click.Context,
    mode: str,
    files: tuple[str, ...],
    batch: int | None,
    debug: bool,
) -> None:
    """Diff fresh extraction against the database."""
    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    if batch is not None:
        if batch < 1:
            raise click.UsageError("--batch must be >= 1")
        if not model:
            raise click.UsageError(
                "--batch requires a model (e.g. llm:gemini/<model>, llm:openai/<model>, or llm:azure/<deployment>)"
            )
        if not model.startswith(_BATCH_MODEL_PREFIXES):
            raise click.UsageError(
                "--batch only supports gemini/, openai/, and azure/ models"
            )

    db_path = _require_db(ctx)
    try:
        gz_files = util.collect_gz_files(list(files))
    except ValueError as e:
        raise click.UsageError(str(e))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    run_dir: str = _ensure_run_dir(ctx)
    s = store.Store.create(db_path)
    t0 = time.monotonic()
    batch_result = _run_diff_db(
        gz_files, parsed_mode, model, run_dir, debug, s, batch_size=batch
    )
    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, 0, elapsed)
    if rc != 0:
        sys.exit(rc)


@diff.command("extractors")
@click.option(
    "--batch",
    type=int,
    default=None,
    help="Batch size for provider batch API (gemini/, openai/, and azure/ models).",
)
@click.argument("spec")
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def diff_extractors_cmd(
    ctx: click.Context,
    batch: int | None,
    spec: str,
    files: tuple[str, ...],
) -> None:
    """Compare two extractors head-to-head.

    SPEC is A..B format (e.g. llm:openai/gpt-5-mini..llm:openai/gpt-5).
    """
    if ".." not in spec:
        raise click.UsageError(
            f"invalid spec: {spec!r} "
            "(expected A..B, e.g. llm:openai/gpt-5-mini..llm:openai/gpt-5)"
        )
    parts = spec.split("..", 1)
    try:
        left = _parse_mode(parts[0])
        right = _parse_mode(parts[1])
    except ValueError as e:
        raise click.UsageError(str(e))

    if batch is not None:
        if batch < 1:
            raise click.UsageError("--batch must be >= 1")
        _, left_model = left
        _, right_model = right
        has_batch_side = any(
            m is not None and m.startswith(_BATCH_MODEL_PREFIXES)
            for m in (left_model, right_model)
        )
        if not has_batch_side:
            raise click.UsageError(
                "--batch requires at least one side with a batch-capable model "
                "(gemini/, openai/, or azure/)"
            )

    try:
        gz_files = util.collect_gz_files(list(files))
    except ValueError as e:
        raise click.UsageError(str(e))
    if not gz_files:
        raise click.UsageError("No .gz files found.")

    run_dir: str = _ensure_run_dir(ctx)
    t0 = time.monotonic()
    batch_result = _run_diff_extractors(
        gz_files, left, right, run_dir, batch_size=batch
    )
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
@click.option("--distro", default=None, help="Filter by distro (e.g. ubuntu).")
@click.option("--release", default=None, help="Filter by release (e.g. 26.04).")
@click.pass_context
def show_manpage(
    ctx: click.Context, name: str, raw: bool, distro: str | None, release: str | None
) -> None:
    """Look up a command and display its extracted options."""
    if (distro is None) != (release is None):
        raise click.UsageError("--distro and --release must be used together.")
    s = store.Store(_require_db(ctx, must_exist=True), read_only=True)
    try:
        results = s.find_man_page(name, distro=distro, release=release)
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
        if opt.prefix:
            click.echo(f"      prefix: {opt.prefix}")
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


@show.command("events")
@click.option("-n", "limit", default=10, help="Number of events to show.")
@click.pass_context
def show_events(ctx: click.Context, limit: int) -> None:
    """Show recent db_events."""
    import datetime

    import humanize

    db_path = _require_db(ctx, must_exist=True)
    s = store.Store(db_path, read_only=True)
    events = s.get_events(limit=limit)
    s.close()
    if not events:
        click.echo("No events recorded.")
        return
    events.reverse()
    for i, ev in enumerate(events):
        if i > 0:
            click.echo()
        dt = datetime.datetime.fromisoformat(ev["timestamp"])
        short_ts = dt.strftime("%Y-%m-%d %H:%M")
        click.echo(f"{short_ts} ({humanize.naturaltime(dt)})")
        click.echo(f"  event:    {ev['event']}")
        if ev["event"] == "extraction":
            report = ExtractionReport(**ev["metadata"])
            if report.config.mode:
                click.echo(f"  mode:     {report.config.mode}")
            if report.config.model:
                click.echo(f"  model:    {report.config.model}")
            sm = report.summary
            click.echo(
                f"  result:   ok={sm.succeeded} skip={sm.skipped} fail={sm.failed}"
            )
            dm = report.db_after.manpages - report.db_before.manpages
            dmap = report.db_after.mappings - report.db_before.mappings
            click.echo(
                f"  db:       {report.db_after.manpages}({dm:+d})"
                f" mappings={report.db_after.mappings}({dmap:+d})"
            )
            click.echo(f"  reason:   {report.reason or '(no reason provided)'}")
        else:
            meta = ev.get("metadata", {})
            for k, v in meta.items():
                if k in ("version", "command"):
                    continue
                click.echo(f"  {k}:  {v}")


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
