"""
CLI entry point for LLM-based man page extraction.

Usage:
    python -m explainshell.llm_manager --model <model> [options] files...
"""

import argparse
import glob
import logging
import os
import sys

from explainshell import config, errors, store
from explainshell.llm_extractor import ExtractionError, extract
from explainshell.manager import Manager

logger = logging.getLogger(__name__)

# ManPage-level fields to compare in diff mode.
_MP_FIELDS = ("name", "synopsis", "aliases", "nested_cmd", "multi_cmd", "partial_match")

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


def _normalize(field, val):
    """Normalize a field value so that None and False compare equal."""
    if field in _FALSY_EQUIVALENT and not val:
        return None
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


def _diff_manpage(stored_mp, fresh_mp):
    """Print a unified-diff-style comparison between stored and fresh ManPage."""
    has_diff = False

    # Compare top-level fields.
    for field in _MP_FIELDS:
        old_val = _normalize(field, getattr(stored_mp, field))
        new_val = _normalize(field, getattr(fresh_mp, field))
        if old_val != new_val:
            has_diff = True
            print(f"  {_BOLD}{field}:{_RESET}")
            print(_fmt_value(old_val, "    - ", _RED))
            print(_fmt_value(new_val, "    + ", _GREEN))

    # Build option indexes keyed by _option_key.
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
            diffs = []
            for field in _OPT_FIELDS:
                old_val = _normalize(field, getattr(s_opt, field))
                new_val = _normalize(field, getattr(f_opt, field))
                if old_val != new_val:
                    diffs.append((field, old_val, new_val))
            if diffs:
                changed_options.append((_fmt_flags(s_opt), diffs))
            else:
                changed_options.append((_fmt_flags(s_opt), None))
        elif f_opt:
            added_options.append(f_opt)
        else:
            removed_options.append(s_opt)

    if changed_options or added_options or removed_options:
        print(f"  {_BOLD}options:{_RESET}")

    for label, diffs in changed_options:
        if diffs is None:
            print(f"    {_DIM}{label}  (unchanged){_RESET}")
        else:
            has_diff = True
            print(f"    {_CYAN}{_BOLD}{label}{_RESET}")
            for field, old_val, new_val in diffs:
                print(f"      {field}:")
                print(_fmt_value(old_val, "        - ", _RED))
                print(_fmt_value(new_val, "        + ", _GREEN))

    for opt in added_options:
        has_diff = True
        print(f"    {_GREEN}{_BOLD}+ {_fmt_flags(opt)}   (added){_RESET}")
        _print_option_detail(opt, prefix="    ", color=_GREEN)

    for opt in removed_options:
        has_diff = True
        print(f"    {_RED}{_BOLD}- {_fmt_flags(opt)}   (removed){_RESET}")
        _print_option_detail(opt, prefix="    ", color=_RED)

    if not has_diff:
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
                for f in glob.glob(os.path.join(path, "*.gz"))
            )
        else:
            result.append(os.path.abspath(path))
    return result


def main(args):
    logging.basicConfig(level=getattr(logging, args.log.upper()))

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

    s = store.Store(db_path) if not args.dry_run or args.diff else None
    if s and args.drop:
        s.drop(confirm=True)

    added = 0
    skipped = 0
    failed = 0

    from explainshell import manpage as _manpage

    for gz_path in gz_files:
        short_path = os.path.basename(gz_path)
        name = _manpage.extract_name(gz_path)

        if s and not args.diff and not args.overwrite and _already_stored(s, short_path, name):
            logger.info("skipping %s (already stored)", short_path)
            skipped += 1
            continue

        try:
            debug_dir = args.debug_dir if args.dry_run else None
            mp = extract(gz_path, args.model, debug_dir=debug_dir)
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
                print(f"  partial_match: {mp.partial_match}")
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
        except ExtractionError as e:
            logger.error("failed to process %s: %s", short_path, e)
            failed += 1
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("unexpected error processing %s: %s", short_path, e)
            failed += 1

    # update multi-cmd mappings (only when writing to DB)
    if s and added > 0 and not args.dry_run and not args.diff:
        m = Manager.__new__(Manager)
        m.store = s
        m.findmulti_cmds()

    dry_run_note = " (dry run)" if args.dry_run else ""
    print(f"Done{dry_run_note}: {added} extracted, {skipped} skipped, {failed} failed.")
    return 0 if failed == 0 else 1


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Process man pages with an LLM and store the results."
    )
    parser.add_argument("--model", required=True, help="LiteLLM model string")
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
        action="store_true",
        default=False,
        help="Compare fresh LLM extraction against what is stored in the DB",
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
