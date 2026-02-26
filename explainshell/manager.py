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
import difflib
import glob
import logging
import os
import sys
import time

from explainshell import config, errors, llm_extractor, mandoc_extractor, source_extractor, store, tree_parser

logger = logging.getLogger(__name__)

# ParsedManpage-level fields to compare in diff mode.
_MP_FIELDS = ("name", "synopsis", "aliases", "nested_cmd", "multi_cmd", "dashless_opts")

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


def _print_option_detail(opt, prefix="", color=""):
    """Print all fields of an option (used for added/removed options)."""
    print(f"{color}{prefix}    short: {opt.short}")
    print(f"{prefix}    long: {opt.long}")
    print(f"{prefix}    expects_arg: {opt.expects_arg}")
    if opt.argument:
        print(f"{prefix}    argument: {opt.argument}")
    if opt.nested_cmd:
        print(f"{prefix}    nested_cmd: {opt.nested_cmd}")
    desc = opt.text.strip()
    for line in desc.split("\n"):
        print(f"{prefix}    {line}")
    print(_RESET, end="")


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
            diffs.append({
                "type": "field",
                "label": field,
                "details": [(field, old_val, new_val)],
            })

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
                diffs.append({
                    "type": "option_changed",
                    "label": _fmt_flags(s_opt),
                    "details": opt_diffs,
                })
        elif f_opt:
            diffs.append({
                "type": "option_added",
                "label": _fmt_flags(f_opt),
                "details": f_opt,
            })
        else:
            diffs.append({
                "type": "option_removed",
                "label": _fmt_flags(s_opt),
                "details": s_opt,
            })

    return diffs


def _diff_manpage(stored_mp, fresh_mp):
    """Print a unified-diff-style comparison between stored and fresh ParsedManpage."""
    diffs = compare_manpages(stored_mp, fresh_mp)

    # Separate field-level diffs for display.
    field_diffs = [d for d in diffs if d["type"] == "field"]

    for d in field_diffs:
        field = d["label"]
        _, old_val, new_val = d["details"][0]
        print(f"  {_BOLD}{field}:{_RESET}")
        text_diff = _fmt_text_diff(old_val, new_val, "    ")
        if text_diff:
            print(text_diff)
        else:
            print(_fmt_value(old_val, "    - ", _RED))
            print(_fmt_value(new_val, "    + ", _GREEN))

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
            changed_options.append((_fmt_flags(s_opt), opt_diffs if opt_diffs else None))
        elif f_opt:
            added_options.append(f_opt)
        else:
            removed_options.append(s_opt)

    if changed_options or added_options or removed_options:
        print(f"  {_BOLD}options:{_RESET}")

    for label, opt_field_diffs in changed_options:
        if opt_field_diffs is None:
            print(f"    {_DIM}{label}  (unchanged){_RESET}")
        else:
            print(f"    {_CYAN}{_BOLD}{label}{_RESET}")
            for field, old_val, new_val in opt_field_diffs:
                print(f"      {field}:")
                text_diff = _fmt_text_diff(old_val, new_val, "        ")
                if text_diff:
                    print(text_diff)
                else:
                    print(_fmt_value(old_val, "        - ", _RED))
                    print(_fmt_value(new_val, "        + ", _GREEN))

    for opt in added_options:
        print(f"    {_GREEN}{_BOLD}+ {_fmt_flags(opt)}   (added){_RESET}")
        _print_option_detail(opt, prefix="    ", color=_GREEN)

    for opt in removed_options:
        print(f"    {_RED}{_BOLD}- {_fmt_flags(opt)}   (removed){_RESET}")
        _print_option_detail(opt, prefix="    ", color=_RED)

    if not diffs:
        print(f"  {_DIM}(no changes){_RESET}")


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
            raise ValueError("--mode llm:<model> requires a model name (e.g. llm:gpt-4o)")
        return "llm", model
    if raw.startswith("hybrid:"):
        model = raw[7:]
        if not model:
            raise ValueError("--mode hybrid:<model> requires a model name (e.g. hybrid:gpt-4o)")
        return "hybrid", model
    raise ValueError(
        f"invalid --mode value: {raw!r} "
        f"(expected 'source', 'mandoc', 'llm:<model>', or 'hybrid:<model>')"
    )


def main(args):
    logging.basicConfig(level=getattr(logging, args.log.upper()))

    try:
        mode, model = _parse_mode(args.mode)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.diff != "modes" and not mode:
        print("error: --mode is required (unless --diff modes)", file=sys.stderr)
        return 1

    if args.diff == "modes" and not model:
        print("error: --mode llm:<model> is required when --diff modes", file=sys.stderr)
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

    s = store.Store(db_path) if not args.dry_run or args.diff == "db" else None
    if s and args.drop:
        s.drop(confirm=True)

    added = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    from explainshell import manpage as _manpage

    for gz_path in gz_files:
        short_path = config.source_from_path(gz_path)
        name = _manpage.extract_name(gz_path)

        if s and not args.diff and not args.overwrite and _already_stored(s, short_path, name):
            logger.info("skipping %s (already stored)", short_path)
            skipped += 1
            continue

        if args.diff == "modes":
            print(f"{_ts()} [{short_path}] running source extractor...")
            try:
                source_mp = source_extractor.extract(gz_path)
            except errors.ExtractionError as e:
                logger.error("source extractor failed for %s: %s", short_path, e)
                print(f"=== {short_path} (source vs llm) ===")
                print(f"  {_DIM}(source extractor failed: {e}, skipping){_RESET}")
                failed += 1
                continue
            print(f"{_ts()} [{short_path}] running llm extractor ({model})...")
            try:
                debug_dir = args.debug_dir if args.dry_run else None
                llm_mp = llm_extractor.extract(gz_path, model, debug_dir=debug_dir)
            except errors.ExtractionError as e:
                logger.error("llm extractor failed for %s: %s", short_path, e)
                print(f"=== {short_path} (source vs llm) ===")
                print(f"  {_DIM}(llm extractor failed: {e}, skipping){_RESET}")
                failed += 1
                continue
            print(f"=== {short_path} (source vs llm) ===")
            _diff_manpage(source_mp, llm_mp)
            added += 1
            continue

        try:
            if mode == "source":
                print(f"{_ts()} [{short_path}] extracting (source)...")
                mp = source_extractor.extract(gz_path)
            elif mode == "mandoc":
                print(f"{_ts()} [{short_path}] extracting (mandoc)...")
                mp = mandoc_extractor.extract(gz_path)
            elif mode == "hybrid":
                print(f"{_ts()} [{short_path}] extracting (hybrid)...")
                result = tree_parser.parse_options(gz_path)
                confidence = tree_parser.assess_confidence(result)
                if confidence.confident and result.options:
                    logger.info("hybrid: tree parser confident for %s (%d options)",
                                short_path, len(result.options))
                    mp = mandoc_extractor.build_manpage(gz_path, result.options)
                else:
                    logger.warning("hybrid: falling back to LLM for %s: %s",
                                   short_path, confidence)
                    print(f"{_ts()} [{short_path}] tree parser {confidence}, falling back to LLM ({model})...")
                    debug_dir = args.debug_dir if args.dry_run else None
                    mp = llm_extractor.extract(gz_path, model, debug_dir=debug_dir)
            else:
                print(f"{_ts()} [{short_path}] extracting ({model})...")
                debug_dir = args.debug_dir if args.dry_run else None
                mp = llm_extractor.extract(gz_path, model, debug_dir=debug_dir)
            if args.diff:
                print(f"=== {short_path} ===")
                try:
                    results = s.find_man_page(name)
                    stored_mp = results[0]
                except errors.ProgramDoesNotExist:
                    print("  (not in DB, nothing to diff)")
                    added += 1
                    continue
                _diff_manpage(stored_mp, mp)
                added += 1
            elif s:
                s.add_manpage(mp)
                logger.info("added %s (%d options)", short_path, len(mp.options))
                added += 1
            else:
                print(f"=== {short_path} ({len(mp.options)} option(s)) ===")
                print(f"  name: {mp.name}")
                print(f"  synopsis: {mp.synopsis}")
                print(f"  aliases: {mp.aliases}")
                print(f"  nested_cmd: {mp.nested_cmd}")
                print(f"  multi_cmd: {mp.multi_cmd}")
                print(f"  dashless_opts: {mp.dashless_opts}")
                print()
                for i, opt in enumerate(mp.options):
                    if i > 0:
                        print()
                    print(f"  [{i}]")
                    print(f"      short: {opt.short}")
                    print(f"      long: {opt.long}")
                    print(f"      expects_arg: {opt.expects_arg}")
                    if opt.argument:
                        print(f"      argument: {opt.argument}")
                    if opt.nested_cmd:
                        print(f"      nested_cmd: {opt.nested_cmd}")
                    # Print description, indented
                    desc = opt.text.strip()
                    lines = desc.split("\n")
                    for line in lines:
                        print(f"      {line}")
                added += 1
        except errors.ExtractionError as e:
            logger.error("failed to process %s: %s", short_path, e)
            failed += 1
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("unexpected error processing %s: %s", short_path, e)
            failed += 1

    # update multi-cmd mappings (only when writing to DB)
    if s and added > 0 and not args.dry_run and not args.diff:
        s.update_multi_cmd_mappings()

    elapsed = time.monotonic() - t0
    dry_run_note = " (dry run)" if args.dry_run else ""
    print(f"Done{dry_run_note}: {added} extracted, {skipped} skipped, {failed} failed. Total time: {_fmt_elapsed(elapsed)}")
    return 0 if failed == 0 else 1


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Extract man page options and store the results."
    )
    parser.add_argument(
        "--mode",
        help="Extraction mode: 'source', 'mandoc', 'llm:<model>', or 'hybrid:<model>'. Required unless --diff modes.",
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
        choices=["db", "modes"],
        help="Diff mode: 'db' (default) compares fresh extraction against the DB; "
             "'modes' compares source vs LLM extraction against each other",
    )
    parser.add_argument(
        "--debug-dir",
        default="debug-output",
        help="Directory for debug files in dry-run mode (default: debug-output)",
    )
    parser.add_argument(
        "--log",
        default="WARNING",
        help="Log level (default: WARNING)",
    )
    parser.add_argument("files", nargs="+", help=".gz files or directories")
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(main(args))
