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

    if args.drop:
        answer = input("Really drop all data? (y/n) ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    gz_files = _collect_gz_files(args.files)
    if not gz_files:
        print("No .gz files found.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Dry run — would process {len(gz_files)} file(s):")
        for f in gz_files:
            print(f"  {f}")
        return 0

    s = store.Store(db_path)
    if args.drop:
        s.drop(confirm=True)

    added = 0
    skipped = 0
    failed = 0

    for gz_path in gz_files:
        short_path = os.path.basename(gz_path)
        from explainshell import manpage as _manpage
        name = _manpage.extract_name(gz_path)

        if not args.overwrite and _already_stored(s, short_path, name):
            logger.info("skipping %s (already stored)", short_path)
            skipped += 1
            continue

        try:
            mp = extract(gz_path, args.model)
            s.add_manpage(mp)
            logger.info("added %s (%d options)", short_path, len(mp.options))
            added += 1
        except ExtractionError as e:
            logger.error("failed to process %s: %s", short_path, e)
            failed += 1

    # update multi-cmd mappings
    if added > 0:
        m = Manager.__new__(Manager)
        m.store = s
        m.findmulti_cmds()

    print(f"Done: {added} added, {skipped} skipped, {failed} failed.")
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
        help="Show what would be processed, no LLM calls",
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
