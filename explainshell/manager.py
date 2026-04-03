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
    llm:<model>         Use an LLM (e.g. llm:openai/gpt-5-mini, llm:codex/gpt-5.4-mini)
    hybrid:<model>      Try tree parser first, fall back to LLM when confidence is low

Reasoning effort can be appended to the model string:
    openai/<model>/<effort>     e.g. llm:openai/o3/medium (low, medium, high)
    azure/<model>/<effort>      e.g. llm:azure/o3/high
    gemini/<model>/<budget>     e.g. llm:gemini/gemini-2.5-flash/8192 (thinking token budget)
    codex/<model>/<effort>      e.g. llm:codex/o3/high
    <litellm-model>/<effort>    e.g. llm:anthropic/claude-sonnet-4-20250514/medium (low, medium, high)
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
from explainshell.extraction.manifest import (
    BatchManifest,
    BatchManifestWriter,
    failed_batches,
    load_manifest,
)
from explainshell.extraction.report import (
    DbCounts,
    ExtractConfig,
    ExtractSummary,
    ExtractionReport,
    GitInfo,
)
from explainshell.extraction.runner import (
    run,
    run_sequential,
)

logger = logging.getLogger("explainshell.manager")

# ANSI color helpers.
_RED = "\033[31m"
_GREEN = "\033[32m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_BATCH_MODEL_PREFIXES = ("gemini/", "openai/", "azure/")


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
    run_dir: str,
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
    run_dir: str,
    debug: bool,
    s: store.Store,
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

    return run_sequential(ext, gz_files, on_start=on_start, on_result=on_result)


def _run_dry_run(
    gz_files: list[str],
    mode: str,
    model: str | None,
    run_dir: str,
) -> BatchResult:
    """Run --dry-run mode: extract but don't write to DB."""
    cfg = ExtractorConfig(model=model, run_dir=run_dir, debug=True)
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


def _setup_logging(log_level_str: str) -> str:
    """Configure logging for the CLI.

    Creates a timestamped run directory under ``logs/`` and writes
    ``run.log`` inside it.  Returns the run directory path so that
    other artifacts (debug files, manifests) can be placed alongside
    the log.
    """
    import datetime

    log_level = getattr(logging, log_level_str.upper())
    fmt = "%(asctime)s %(levelname)-5s [%(name)s:%(lineno)d] %(message)s"
    datefmt = "%H:%M:%S"

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format=fmt,
        datefmt=datefmt,
    )

    logs_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(logs_root, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "run.log")

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.getLogger("explainshell").addHandler(file_handler)

    logging.getLogger("explainshell").setLevel(log_level)
    logger.info("command line: %s", " ".join(sys.argv))
    logger.info("logging to %s", log_path)

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
    ctx.obj["run_dir"] = _setup_logging(log_level)


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
def extract(
    ctx: click.Context,
    mode: str,
    files: tuple[str, ...],
    dry_run: bool,
    overwrite: bool,
    drop: bool,
    jobs: int,
    batch: int | None,
    debug: bool,
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
                "--batch requires a model (e.g. llm:gemini/<model>, llm:openai/<model>, or llm:azure/<deployment>)"
            )
        if not model.startswith(_BATCH_MODEL_PREFIXES):
            raise click.UsageError(
                "--batch only supports gemini/, openai/, and azure/ models"
            )
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

    run_dir: str = ctx.obj["run_dir"]

    if dry_run:
        t0 = time.monotonic()
        batch_result = _run_dry_run(gz_files, parsed_mode, model, run_dir)
        elapsed = time.monotonic() - t0
        rc = _log_summary(batch_result, 0, elapsed, dry_run=True)
        if rc != 0:
            sys.exit(rc)
        return

    db_path = _require_db(ctx)
    s = store.Store.create(db_path)
    if drop:
        s.drop(confirm=True)

    db_before = s.counts()
    t0 = time.monotonic()
    prefilter_skipped = 0
    symlinks_mapped = 0
    content_deduped = 0
    symlink_files: list[tuple[str, str, str]] = []  # (gz_path, source, canonical)
    content_dup_files: list[tuple[str, str, str]] = []  # same shape

    cfg = ExtractorConfig(model=model, run_dir=run_dir, debug=debug)
    extractor = make_extractor(parsed_mode, cfg)

    # Pre-filter already-stored files, separate symlinks, and deduplicate
    # content-identical files (e.g. cross-compiler gcc variants).
    # Dedup is scoped to (hash, distro, release) so cross-release lookups
    # are not broken.  When --overwrite is set, we skip seeding from the DB
    # so that every canonical gets re-extracted; in-run dedup still applies.
    from explainshell.extraction.common import gz_sha256

    def _dedup_key(sha: str, source: str) -> str:
        """Build a dedup key scoped to distro/release/section."""
        # source format: "distro/release/section/file.gz"
        prefix = source.rsplit("/", 1)[0]  # "distro/release/section"
        return f"{sha}:{prefix}"

    hash_to_canonical: dict[str, str] = {}
    if not overwrite:
        for sha, source in s.known_sha256s().items():
            hash_to_canonical[_dedup_key(sha, source)] = source

    work_files: list[str] = []
    for gz_path in gz_files:
        short_path = config.source_from_path(gz_path)
        if not overwrite and s.has_manpage_source(short_path):
            logger.debug("skipping %s (already stored)", short_path)
            prefilter_skipped += 1
            continue

        if os.path.islink(gz_path):
            canonical_path = os.path.realpath(gz_path)
            canonical_source = config.source_from_path(canonical_path)
            if canonical_source != short_path:
                symlink_files.append((gz_path, short_path, canonical_source))
                continue

        h = gz_sha256(gz_path)
        key = _dedup_key(h, short_path)
        if key in hash_to_canonical:
            content_dup_files.append((gz_path, short_path, hash_to_canonical[key]))
            continue
        hash_to_canonical[key] = short_path
        work_files.append(gz_path)

    if prefilter_skipped:
        logger.info("skipped %d already stored file(s)", prefilter_skipped)
    if content_dup_files:
        logger.info("deduplicated %d content-identical file(s)", len(content_dup_files))

    extract_total = len(work_files) + prefilter_skipped

    start_counter = {"n": 0}
    result_counter = {"n": 0}
    counter_lock = threading.Lock()

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
        if entry.outcome == ExtractionOutcome.SUCCESS:
            s.add_manpage(entry.mp, entry.raw)
            logger.info(
                "%s [%s] done: %d option(s)",
                progress,
                short_path,
                len(entry.mp.options),
            )
        elif entry.outcome == ExtractionOutcome.SKIPPED:
            logger.info(
                "%s [%s] skipped: %s",
                progress,
                short_path,
                entry.error or "unknown reason",
            )
        elif entry.outcome == ExtractionOutcome.FAILED:
            logger.error(
                "%s [%s] FAILED: %s",
                progress,
                short_path,
                entry.error or "unknown error",
            )

    manifest = None
    if batch is not None:
        manifest_path = os.path.join(run_dir, "batch-manifest.json")
        manifest = BatchManifestWriter(manifest_path, model=model, batch_size=batch)

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
                logger.debug(
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
            drop=drop,
            jobs=jobs,
            batch_size=batch,
            debug=debug,
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
        batch_manifest=manifest.to_dict() if manifest is not None else None,
    )
    _write_report(run_dir, report)

    if rc != 0:
        sys.exit(rc)


# ---------------------------------------------------------------------------
# salvage command
# ---------------------------------------------------------------------------


def _run_salvage(
    extractor: BatchExtractor,
    manifest_data: BatchManifest,
    s: store.Store | None,
    dry_run: bool,
) -> BatchResult:
    """Salvage partial results from failed batches using the manifest.

    For each failed batch, retrieves partial results from the provider,
    re-prepares only the files from that batch, and finalizes them.
    """
    bp = extractor.batch_provider
    entries = failed_batches(manifest_data)
    if not entries:
        logger.info("no failed batches in manifest")
        return BatchResult()

    total_batches = manifest_data.total_batches or "?"
    logger.info(
        "found %d failed batch(es) out of %s total",
        len(entries),
        total_batches,
    )

    result = BatchResult()
    for entry in entries:
        batch_idx = entry.batch_idx
        batch_id = entry.batch_id

        if batch_id is None:
            logger.warning(
                "batch %d has no batch_id (submit failed), skipping",
                batch_idx,
            )
            result.n_failed += len(entry.files)
            continue

        logger.info(
            "salvaging batch %d/%s (%s, %d file(s))...",
            batch_idx,
            total_batches,
            batch_id,
            len(entry.files),
        )

        try:
            if entry.status == "submitted":
                # Batch may still be running — poll to terminal state first.
                logger.info("batch %d still in submitted state, polling...", batch_idx)
                client = bp.make_poll_client()
                job = bp.poll_batch(client, batch_id, poll_interval=30, stop_event=None)
            else:
                # Already terminal (failed/expired/cancelled) — just retrieve.
                job = bp.retrieve_batch(batch_id)
        except Exception as e:
            logger.error("failed to retrieve batch %s: %s", batch_id, e)
            result.n_failed += len(entry.files)
            continue

        try:
            collected = bp.collect_results(job)
        except Exception as e:
            logger.error("failed to collect results for batch %s: %s", batch_id, e)
            result.n_failed += len(entry.files)
            continue

        result.stats.input_tokens += collected.usage.input_tokens
        result.stats.output_tokens += collected.usage.output_tokens
        result.stats.reasoning_tokens += collected.usage.reasoning_tokens

        logger.info(
            "batch %d: collected %d result(s) from provider",
            batch_idx,
            len(collected.responses),
        )

        for item_idx, gz_path in enumerate(entry.files):
            short_path = config.source_from_path(gz_path)

            # Skip files already in DB (from a prior successful run or partial salvage).
            if not dry_run and s is not None and s.has_manpage_source(short_path):
                logger.info("[%s] already in DB, skipping", short_path)
                result.n_skipped += 1
                continue

            try:
                prepared = extractor.prepare(gz_path)
            except Exception as e:
                logger.error("[%s] failed to prepare: %s", short_path, e)
                result.n_failed += 1
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
                finalized = extractor.finalize(gz_path, prepared, responses)
            except Exception as e:
                logger.error("[%s] failed to finalize: %s", short_path, e)
                result.n_failed += 1
                continue

            if not dry_run and s is not None:
                s.add_manpage(finalized.mp, finalized.raw)
            logger.info(
                "[%s] salvaged: %d option(s)", short_path, len(finalized.mp.options)
            )
            result.stats += finalized.stats
            result.n_succeeded += 1

    return result


@cli.command()
@click.option(
    "-m",
    "--mode",
    required=True,
    help="Must match the original run's mode (e.g. llm:openai/gpt-5-mini or llm:azure/my-deployment).",
)
@click.option(
    "--manifest",
    required=True,
    type=click.Path(exists=True),
    help="Path to batch-manifest.json from the failed extract --batch run.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be salvaged without writing to DB."
)
@click.option(
    "--debug",
    "debug",
    is_flag=True,
    help="Write full prompt/response debug artifacts.",
)
@click.pass_context
def salvage(
    ctx: click.Context,
    mode: str,
    manifest: str,
    dry_run: bool,
    debug: bool,
) -> None:
    """Salvage partial results from failed batch extraction runs.

    Reads the batch manifest to identify failed batches, then retrieves
    and finalizes their results from the provider.
    """
    from pydantic import ValidationError

    from explainshell.extraction.llm.extractor import BatchExtractor

    try:
        parsed_mode, model = _parse_mode(mode)
    except ValueError as e:
        raise click.UsageError(str(e))

    if parsed_mode != "llm" or not model:
        raise click.UsageError("salvage only works with llm:<model> mode")
    if not model.startswith(_BATCH_MODEL_PREFIXES):
        raise click.UsageError(
            "salvage only supports gemini/, openai/, and azure/ models"
        )

    try:
        manifest_data = load_manifest(manifest, expected_model=model)
    except (ValidationError, ValueError) as e:
        raise click.UsageError(str(e))

    if not dry_run:
        db_path = _require_db(ctx)
        s = store.Store.create(db_path)
    else:
        db_path = None
        s = None

    run_dir: str = ctx.obj["run_dir"]
    cfg = ExtractorConfig(model=model, run_dir=run_dir, debug=debug)
    extractor = make_extractor(parsed_mode, cfg)
    if not isinstance(extractor, BatchExtractor):
        raise click.UsageError("extractor does not support batch mode")

    t0 = time.monotonic()
    batch_result = _run_salvage(extractor, manifest_data, s, dry_run)

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
    dry_run: bool,
    debug: bool,
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

    run_dir: str = ctx.obj["run_dir"]
    s = store.Store.create(db_path)
    t0 = time.monotonic()
    batch_result = _run_diff_db(
        gz_files, parsed_mode, model, run_dir, dry_run or debug, s
    )
    elapsed = time.monotonic() - t0
    rc = _log_summary(batch_result, 0, elapsed)
    if rc != 0:
        sys.exit(rc)


@diff.command("extractors")
@click.argument("spec")
@click.argument("files", nargs=-1, required=True)
@click.pass_context
def diff_extractors_cmd(
    ctx: click.Context,
    spec: str,
    files: tuple[str, ...],
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

    run_dir: str = ctx.obj["run_dir"]
    t0 = time.monotonic()
    batch_result = _run_diff_extractors(gz_files, left, right, run_dir)
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
