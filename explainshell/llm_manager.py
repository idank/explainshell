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

    s = store.Store(db_path) if not args.dry_run else None
    if s and args.drop:
        s.drop(confirm=True)

    added = 0
    skipped = 0
    failed = 0

    from explainshell import manpage as _manpage

    for gz_path in gz_files:
        short_path = os.path.basename(gz_path)
        name = _manpage.extract_name(gz_path)

        if s and not args.overwrite and _already_stored(s, short_path, name):
            logger.info("skipping %s (already stored)", short_path)
            skipped += 1
            continue

        try:
            debug_dir = args.debug_dir if args.dry_run else None
            mp = extract(gz_path, args.model, debug_dir=debug_dir)
            if s:
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
    if s and added > 0:
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
