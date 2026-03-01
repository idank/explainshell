"""
CLI entry point for man page extraction.

Usage:
    python -m explainshell.manager --mode <mode> [options] files...

Modes:
    source              Use the roff parser
    mandoc              Use mandoc -T tree parser
    llm:<model>         Use an LLM via LiteLLM (e.g. llm:gpt-4o)
    hybrid:<model>      Try tree parser first, fall back to LLM when confidence is low
"""

import argparse
import concurrent.futures
import difflib
import glob
import logging
import os
import sys
import time
from dataclasses import dataclass

from explainshell import (
    config,
    errors,
    llm_extractor,
    mandoc_extractor,
    source_extractor,
    store,
)

logger = logging.getLogger(__name__)

# ParsedManpage-level fields to compare in diff mode.
_MP_FIELDS = (
    "name",
    "synopsis",
    "aliases",
    "nested_cmd",
    "multi_cmd",
    "dashless_opts",
    "extractor",
    "extraction_meta",
)

# Per-option fields to compare in diff mode.
_OPT_FIELDS = ("expects_arg", "argument", "nested_cmd", "text")

# Fields where None and False should be treated as equivalent.
_FALSY_EQUIVALENT = {"nested_cmd", "argument"}

# ANSI color helpers.
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


@dataclass
class _FileResult:
    outcome: str  # "added", "skipped", "failed"
    output_lines: list  # collected print output
    mp: object = None  # ParsedManpage for deferred DB write


def _ts():
    """Return a bracketed timestamp string, e.g. [14:05:23]."""
    return time.strftime("[%H:%M:%S]")


def _fmt_elapsed(seconds):
    """Format elapsed seconds as a human-readable string."""
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _normalize(field, val):
    """Normalize a field value so that None and False compare equal.

    Also sorts alias lists so that non-deterministic ordering from lexgrog
    does not produce spurious diffs.
    """
    if field in _FALSY_EQUIVALENT and not val:
        return None
    if field == "aliases" and isinstance(val, list):
        return sorted(val)
    return val


def _option_key(opt):
    """Return a hashable key for matching options between stored and fresh."""
    if opt.short or opt.long:
        return (tuple(sorted(opt.short)), tuple(sorted(opt.long)))
    # Positional argument – match by argument name.
    return ("positional", opt.argument)


def _fmt_flags(opt):
    """Human-readable flag label like [-a, --all]."""
    parts = list(opt.short) + list(opt.long)
    if not parts:
        return f"(positional: {opt.argument})"
    return "[" + ", ".join(parts) + "]"


def _fmt_value(val, indent, color):
    """Format a value, printing each line of multi-line strings with the given color."""
    s = str(val)
    lines = s.split("\n")
    if len(lines) == 1:
        return f"{color}{indent}{s}{_RESET}"
    # Multi-line: first line on same row, rest indented with a continuation marker.
    out = [f"{color}{indent}{lines[0]}"]
    for line in lines[1:]:
        out.append(f"{indent}  {line}")
    out[-1] += _RESET
    return "\n".join(out)


def _fmt_text_diff(old_text, new_text, indent):
    """Format a unified diff for multiline text fields, showing only changed lines."""
    old_lines = str(old_text).splitlines(keepends=True)
    new_lines = str(new_text).splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, n=1))
    if not diff:
        return None
    out = []
    for line in diff[2:]:  # skip --- and +++ headers
        if line.startswith("@@"):
            continue
        text = line[1:].rstrip("\n")
        if line.startswith("-"):
            out.append(f"{_RED}{indent}- {text}{_RESET}")
        elif line.startswith("+"):
            out.append(f"{_GREEN}{indent}+ {text}{_RESET}")
        else:
            out.append(f"{_DIM}{indent}  {text}{_RESET}")
    # Strip leading/trailing blank context lines.
    blank = f"{_DIM}{indent}  {_RESET}"
    while out and out[0] == blank:
        out.pop(0)
    while out and out[-1] == blank:
        out.pop()
    return "\n".join(out)


def _option_detail_lines(opt, prefix="", color=""):
    """Return formatted lines for all fields of an option (used for added/removed options)."""
    lines = []
    lines.append(f"{color}{prefix}    short: {opt.short}")
    lines.append(f"{prefix}    long: {opt.long}")
    lines.append(f"{prefix}    expects_arg: {opt.expects_arg}")
    if opt.argument:
        lines.append(f"{prefix}    argument: {opt.argument}")
    if opt.nested_cmd:
        lines.append(f"{prefix}    nested_cmd: {opt.nested_cmd}")
    desc = opt.text.strip()
    for line in desc.split("\n"):
        lines.append(f"{prefix}    {line}")
    lines.append(_RESET)
    return lines


def compare_manpages(stored_mp, fresh_mp, skip_fields=()):
    """Compare two ParsedManpage objects and return a list of structured diff entries.

    Each entry is a dict with:
      - "type": "field" | "option_changed" | "option_added" | "option_removed"
      - "label": human-readable label
      - "details": list of (field, old_val, new_val) tuples (for field/option_changed)
                   or option object (for added/removed)

    *skip_fields* is an optional iterable of top-level field names to ignore.
    """
    diffs = []
    skip = set(skip_fields)

    # Compare top-level fields.
    for field in _MP_FIELDS:
        if field in skip:
            continue
        old_val = _normalize(field, getattr(stored_mp, field))
        new_val = _normalize(field, getattr(fresh_mp, field))
        if old_val != new_val:
            diffs.append(
                {
                    "type": "field",
                    "label": field,
                    "details": [(field, old_val, new_val)],
                }
            )

    # Build option indexes keyed by _option_key.
    stored_opts = {_option_key(o): o for o in stored_mp.options}
    fresh_opts = {_option_key(o): o for o in fresh_mp.options}

    all_keys = list(dict.fromkeys(list(stored_opts.keys()) + list(fresh_opts.keys())))

    for key in all_keys:
        s_opt = stored_opts.get(key)
        f_opt = fresh_opts.get(key)

        if s_opt and f_opt:
            opt_diffs = []
            for field in _OPT_FIELDS:
                old_val = _normalize(field, getattr(s_opt, field))
                new_val = _normalize(field, getattr(f_opt, field))
                if old_val != new_val:
                    opt_diffs.append((field, old_val, new_val))
            if opt_diffs:
                diffs.append(
                    {
                        "type": "option_changed",
                        "label": _fmt_flags(s_opt),
                        "details": opt_diffs,
                    }
                )
        elif f_opt:
            diffs.append(
                {
                    "type": "option_added",
                    "label": _fmt_flags(f_opt),
                    "details": f_opt,
                }
            )
        else:
            diffs.append(
                {
                    "type": "option_removed",
                    "label": _fmt_flags(s_opt),
                    "details": s_opt,
                }
            )

    return diffs


def _diff_manpage(stored_mp, fresh_mp):
    """Return a list of lines with a unified-diff-style comparison between two ParsedManpages."""
    diffs = compare_manpages(stored_mp, fresh_mp)
    out = []

    # Separate field-level diffs for display.
    field_diffs = [d for d in diffs if d["type"] == "field"]

    for d in field_diffs:
        field = d["label"]
        _, old_val, new_val = d["details"][0]
        out.append(f"  {_BOLD}{field}:{_RESET}")
        text_diff = _fmt_text_diff(old_val, new_val, "    ")
        if text_diff:
            out.append(text_diff)
        else:
            out.append(_fmt_value(old_val, "    - ", _RED))
            out.append(_fmt_value(new_val, "    + ", _GREEN))

    # Rebuild changed/added/removed lists for option display, including unchanged.
    stored_opts = {_option_key(o): o for o in stored_mp.options}
    fresh_opts = {_option_key(o): o for o in fresh_mp.options}
    all_keys = list(dict.fromkeys(list(stored_opts.keys()) + list(fresh_opts.keys())))

    changed_options = []
    added_options = []
    removed_options = []

    for key in all_keys:
        s_opt = stored_opts.get(key)
        f_opt = fresh_opts.get(key)
        if s_opt and f_opt:
            opt_diffs = []
            for field in _OPT_FIELDS:
                old_val = _normalize(field, getattr(s_opt, field))
                new_val = _normalize(field, getattr(f_opt, field))
                if old_val != new_val:
                    opt_diffs.append((field, old_val, new_val))
            changed_options.append(
                (_fmt_flags(s_opt), opt_diffs if opt_diffs else None)
            )
        elif f_opt:
            added_options.append(f_opt)
        else:
            removed_options.append(s_opt)

    if changed_options or added_options or removed_options:
        out.append(f"  {_BOLD}options:{_RESET}")

    for label, opt_field_diffs in changed_options:
        if opt_field_diffs is None:
            out.append(f"    {_DIM}{label}  (unchanged){_RESET}")
        else:
            out.append(f"    {_CYAN}{_BOLD}{label}{_RESET}")
            for field, old_val, new_val in opt_field_diffs:
                out.append(f"      {field}:")
                text_diff = _fmt_text_diff(old_val, new_val, "        ")
                if text_diff:
                    out.append(text_diff)
                else:
                    out.append(_fmt_value(old_val, "        - ", _RED))
                    out.append(_fmt_value(new_val, "        + ", _GREEN))

    for opt in added_options:
        out.append(f"    {_GREEN}{_BOLD}+ {_fmt_flags(opt)}   (added){_RESET}")
        out.extend(_option_detail_lines(opt, prefix="    ", color=_GREEN))

    for opt in removed_options:
        out.append(f"    {_RED}{_BOLD}- {_fmt_flags(opt)}   (removed){_RESET}")
        out.extend(_option_detail_lines(opt, prefix="    ", color=_RED))

    if not diffs:
        out.append(f"  {_DIM}(no changes){_RESET}")

    return out


def _already_stored(s, short_path, name):
    try:
        results = s.find_man_page(name)
        return any(mp.source == short_path for mp in results)
    except errors.ProgramDoesNotExist:
        return False


def _collect_gz_files(paths):
    result = []
    for path in paths:
        if os.path.isdir(path):
            result.extend(
                os.path.abspath(f)
                for f in glob.glob(os.path.join(path, "**", "*.gz"), recursive=True)
            )
        else:
            result.append(os.path.abspath(path))
    return result


def _parse_mode(raw):
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


def _run_extractor(mode, gz_path, model=None, debug_dir=None):
    """Run a single extractor by mode name and return a ParsedManpage."""
    if mode == "source":
        mp = source_extractor.extract(gz_path)
        mp.extractor = "source"
        mp.extraction_meta = {}
        return mp
    if mode == "mandoc":
        mp = mandoc_extractor.extract(gz_path)
        mp.extractor = "mandoc"
        mp.extraction_meta = {}
        return mp
    if mode == "llm":
        mp = llm_extractor.extract(gz_path, model, debug_dir=debug_dir)
        mp.extractor = "llm"
        mp.extraction_meta = {"model": model}
        return mp
    if mode == "hybrid":
        try:
            mp = mandoc_extractor.extract(gz_path)
            mp.extractor = "mandoc"
            mp.extraction_meta = {}
            return mp
        except errors.LowConfidenceError as e:
            mp = llm_extractor.extract(gz_path, model, debug_dir=debug_dir)
            mp.extractor = "llm"
            mp.extraction_meta = {
                "model": model,
                "fallback": True,
                "fallback_reason": str(e)[:256],
            }
            return mp
    raise ValueError(f"unknown extractor mode: {mode!r}")


def _parse_diff(raw):
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


def _process_one_file(
    gz_path, short_path, name, progress, mode, model,
    is_extractor_diff, diff_left, diff_right, diff_kind,
    dry_run, debug_dir, s,
):
    """Process a single gz file and return a _FileResult.

    Output is collected in result.output_lines instead of printed directly.
    DB writes are deferred via result.mp (written by the caller).
    """
    result = _FileResult(outcome="added", output_lines=[])
    out = result.output_lines.append

    if is_extractor_diff:
        left_mode, left_model = diff_left
        right_mode, right_model = diff_right
        _debug_dir = debug_dir if dry_run else None
        label = f"{left_mode} vs {right_mode}"

        out(f"{_ts()} {progress} [{short_path}] running {left_mode} extractor...")
        try:
            left_mp = _run_extractor(
                left_mode, gz_path, model=left_model, debug_dir=_debug_dir
            )
        except errors.ExtractionError as e:
            logger.error("%s extractor failed for %s: %s", left_mode, short_path, e)
            out(f"=== {short_path} ({label}) ===")
            out(f"  {_DIM}({left_mode} extractor failed: {e}, skipping){_RESET}")
            result.outcome = "failed"
            return result

        right_label = (
            right_mode if not right_model else f"{right_mode} ({right_model})"
        )
        out(
            f"{_ts()} {progress} [{short_path}] running {right_label} extractor..."
        )
        try:
            right_mp = _run_extractor(
                right_mode, gz_path, model=right_model, debug_dir=_debug_dir
            )
        except errors.ExtractionError as e:
            logger.error(
                "%s extractor failed for %s: %s", right_mode, short_path, e
            )
            out(f"=== {short_path} ({label}) ===")
            out(f"  {_DIM}({right_mode} extractor failed: {e}, skipping){_RESET}")
            result.outcome = "failed"
            return result

        out(f"=== {short_path} ({label}) ===")
        result.output_lines.extend(_diff_manpage(left_mp, right_mp))
        return result

    file_t0 = time.monotonic()
    try:
        if mode == "source":
            out(f"{_ts()} {progress} [{short_path}] extracting (source)...")
            mp = source_extractor.extract(gz_path)
            mp.extractor = "source"
            mp.extraction_meta = {}
        elif mode == "mandoc":
            out(f"{_ts()} {progress} [{short_path}] extracting (mandoc)...")
            mp = mandoc_extractor.extract(gz_path)
            mp.extractor = "mandoc"
            mp.extraction_meta = {}
        elif mode == "hybrid":
            out(f"{_ts()} {progress} [{short_path}] extracting (hybrid)...")
            try:
                mp = mandoc_extractor.extract(gz_path)
                mp.extractor = "mandoc"
                mp.extraction_meta = {}
            except errors.LowConfidenceError as e:
                logger.warning(
                    "hybrid: falling back to LLM for %s: %s", short_path, e
                )
                out(
                    f"{_ts()} {progress} [{short_path}] tree parser {e}, falling back to LLM ({model})..."
                )
                _debug_dir = debug_dir if dry_run else None
                mp = llm_extractor.extract(gz_path, model, debug_dir=_debug_dir)
                mp.extractor = "llm"
                mp.extraction_meta = {
                    "model": model,
                    "fallback": True,
                    "fallback_reason": str(e)[:256],
                }
        else:
            out(f"{_ts()} {progress} [{short_path}] extracting ({model})...")
            _debug_dir = debug_dir if dry_run else None
            mp = llm_extractor.extract(gz_path, model, debug_dir=_debug_dir)
            mp.extractor = "llm"
            mp.extraction_meta = {"model": model}

        file_elapsed = _fmt_elapsed(time.monotonic() - file_t0)
        if diff_kind == "db":
            out(f"=== {short_path} ===")
            try:
                results = s.find_man_page(name)
                stored_mp = results[0]
            except errors.ProgramDoesNotExist:
                out("  (not in DB, nothing to diff)")
                return result
            result.output_lines.extend(_diff_manpage(stored_mp, mp))
        elif s:
            result.mp = mp
            out(
                f"{_ts()} {progress} [{short_path}] done: {len(mp.options)} option(s) in {file_elapsed}"
            )
        else:
            out(
                f"=== {short_path} ({len(mp.options)} option(s), {file_elapsed}) ==="
            )
            out(f"  name: {mp.name}")
            out(f"  synopsis: {mp.synopsis}")
            out(f"  aliases: {mp.aliases}")
            out(f"  nested_cmd: {mp.nested_cmd}")
            out(f"  multi_cmd: {mp.multi_cmd}")
            out(f"  dashless_opts: {mp.dashless_opts}")
            out(f"  extractor: {mp.extractor}")
            out(f"  extraction_meta: {mp.extraction_meta}")
            out("")
            for i, opt in enumerate(mp.options):
                if i > 0:
                    out("")
                out(f"  [{i}]")
                out(f"      short: {opt.short}")
                out(f"      long: {opt.long}")
                out(f"      expects_arg: {opt.expects_arg}")
                if opt.argument:
                    out(f"      argument: {opt.argument}")
                if opt.nested_cmd:
                    out(f"      nested_cmd: {opt.nested_cmd}")
                desc = opt.text.strip()
                lines = desc.split("\n")
                for line in lines:
                    out(f"      {line}")
    except errors.ExtractionError as e:
        logger.error("failed to process %s: %s", short_path, e)
        result.outcome = "failed"
    except Exception as e:
        logger.error("unexpected error processing %s: %s", short_path, e)
        result.outcome = "failed"

    return result


def main(args):
    logging.basicConfig(level=getattr(logging, args.log.upper()))

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

    if not is_extractor_diff and args.diff is not None and not mode:
        # --diff db requires --mode
        print("error: --mode is required when using --diff db", file=sys.stderr)
        return 1

    if not is_extractor_diff and not args.diff and not mode:
        print("error: --mode is required", file=sys.stderr)
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

    s = store.Store(db_path) if not args.dry_run or diff_kind == "db" else None
    if s and args.drop:
        s.drop(confirm=True)

    added = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    from explainshell import manpage as _manpage

    def _handle_result(result):
        """Print output lines and write to DB. Returns (added_delta, failed_delta)."""
        for line in result.output_lines:
            print(line)
        if result.mp and s:
            s.add_manpage(result.mp)
        if result.outcome == "added":
            return 1, 0
        if result.outcome == "failed":
            return 0, 1
        return 0, 0

    total = len(gz_files)
    if args.jobs == 1:
        # Sequential processing.
        for file_idx, gz_path in enumerate(gz_files, 1):
            short_path = config.source_from_path(gz_path)
            name = _manpage.extract_name(gz_path)
            progress = f"[{file_idx}/{total}]"

            if (
                s
                and not args.diff
                and not args.overwrite
                and _already_stored(s, short_path, name)
            ):
                logger.info("skipping %s (already stored)", short_path)
                skipped += 1
                continue

            result = _process_one_file(
                gz_path, short_path, name, progress, mode, model,
                is_extractor_diff, diff_left, diff_right, diff_kind,
                args.dry_run, args.debug_dir, s,
            )
            a, f = _handle_result(result)
            added += a
            failed += f
    else:
        # Parallel processing.
        # Pre-filter: check _already_stored in main thread, build work list.
        work_items = []
        for file_idx, gz_path in enumerate(gz_files, 1):
            short_path = config.source_from_path(gz_path)
            name = _manpage.extract_name(gz_path)
            progress = f"[{file_idx}/{total}]"

            if (
                s
                and not args.diff
                and not args.overwrite
                and _already_stored(s, short_path, name)
            ):
                logger.info("skipping %s (already stored)", short_path)
                skipped += 1
                continue

            work_items.append((gz_path, short_path, name, progress))

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)
        try:
            futures = {
                executor.submit(
                    _process_one_file,
                    gz_path, short_path, name, progress, mode, model,
                    is_extractor_diff, diff_left, diff_right, diff_kind,
                    args.dry_run, args.debug_dir, s,
                ): short_path
                for gz_path, short_path, name, progress in work_items
            }
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                a, f = _handle_result(result)
                added += a
                failed += f
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    # update multi-cmd mappings (only when writing to DB)
    if s and added > 0 and not args.dry_run and not args.diff:
        s.update_multi_cmd_mappings()

    elapsed = time.monotonic() - t0
    dry_run_note = " (dry run)" if args.dry_run else ""
    print(
        f"Done{dry_run_note}: {added} extracted, {skipped} skipped, {failed} failed. Total time: {_fmt_elapsed(elapsed)}"
    )
    return 0 if failed == 0 else 1


def _build_parser():
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
        "--log",
        default="WARNING",
        help="Log level (default: WARNING)",
    )
    parser.add_argument("files", nargs="*", help=".gz files or directories")
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(main(args))
