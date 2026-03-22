#!/usr/bin/env python3
"""
Post-process Go pipeline manpage output for explainshell.

Copies man1 and man8 sections from the Go pipeline output directory,
renaming them to 1/ and 8/. Valid symlinks are preserved (with
cross-section targets rewritten for the renamed directories). Broken
symlinks are removed.

Usage:
    python tools/postprocess_ubuntu_archive.py output/manpages.gz/26.04 manpages/ubuntu/26.04
    python tools/postprocess_ubuntu_archive.py --dry-run output/manpages.gz/26.04 manpages/ubuntu/26.04
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Map source section directory names to destination names.
SECTIONS_TO_KEEP: dict[str, str] = {"man1": "1", "man8": "8"}

# Matches a cross-section symlink target like ``../man1/foo.1.gz``.
_CROSS_SECTION_RE = re.compile(r"^\.\./(?P<section>man\d+)/(?P<rest>.+)$")


@dataclass
class Stats:
    files_copied: int = 0
    symlinks_copied: int = 0
    symlinks_rewritten: int = 0
    symlinks_skipped: int = 0
    dirs_skipped: int = 0


def _rewrite_target(target: str, src_dir: Path) -> str | None:
    """Rewrite a symlink *target* for the destination directory layout.

    Returns the (possibly rewritten) target string, or ``None`` if the
    symlink should be skipped (broken or points to a deleted section).
    """
    m = _CROSS_SECTION_RE.match(target)
    if m is None:
        # Same-section relative link (e.g. ``coqc.1.gz``) or an
        # unusual path (deep, absolute).  For same-section links the
        # target name must exist as a real file in the source section
        # directory — that is verified by the caller.  Anything else
        # (absolute paths, ``../../`` chains) is considered broken.
        if target.startswith("/") or target.startswith("../"):
            return None
        return target

    section = m.group("section")
    rest = m.group("rest")

    if section not in SECTIONS_TO_KEEP:
        # Target is in a section we don't keep (e.g. man3, man7).
        return None

    # Verify the target file actually exists in the source tree.
    if not (src_dir / section / rest).exists():
        return None

    return f"../{SECTIONS_TO_KEEP[section]}/{rest}"


def process_section(
    src_section: Path,
    dst_section: Path,
    src_dir: Path,
    stats: Stats,
    *,
    dry_run: bool = False,
) -> None:
    """Copy regular files and fix symlinks from *src_section* into *dst_section*."""
    if not dry_run:
        dst_section.mkdir(parents=True, exist_ok=True)

    for entry in sorted(os.scandir(src_section), key=lambda e: e.name):
        dst_path = dst_section / entry.name

        # Remove any stale file or symlink at the destination so we
        # don't fail trying to write through a broken symlink from a
        # previous run.
        if not dry_run and (dst_path.is_symlink() or dst_path.exists()):
            dst_path.unlink()

        if entry.is_dir(follow_symlinks=False):
            logger.debug("skipping nested directory: %s", entry.name)
            stats.dirs_skipped += 1
            continue

        if entry.is_symlink():
            raw_target = os.readlink(entry.path)
            new_target = _rewrite_target(raw_target, src_dir)

            if new_target is None:
                logger.debug(
                    "skipping broken symlink: %s -> %s", entry.name, raw_target
                )
                stats.symlinks_skipped += 1
                continue

            # For same-section links, verify target exists.
            if new_target == raw_target and not (src_section / raw_target).exists():
                logger.debug(
                    "skipping broken symlink: %s -> %s", entry.name, raw_target
                )
                stats.symlinks_skipped += 1
                continue

            if not dry_run:
                os.symlink(new_target, dst_path)

            if new_target != raw_target:
                logger.debug(
                    "rewriting symlink: %s -> %s (was %s)",
                    entry.name,
                    new_target,
                    raw_target,
                )
                stats.symlinks_rewritten += 1
            else:
                stats.symlinks_copied += 1
            continue

        # Regular file.
        if not dry_run:
            shutil.copy2(entry.path, str(dst_path))
        stats.files_copied += 1


def postprocess(src_dir: Path, dst_dir: Path, *, dry_run: bool = False) -> Stats:
    """Copy and restructure manpages from *src_dir* to *dst_dir*.

    Only sections listed in ``SECTIONS_TO_KEEP`` are copied. Section
    directories are renamed (e.g. ``man1`` → ``1``). Valid symlinks are
    preserved with their targets rewritten when necessary. Broken
    symlinks are removed.
    """
    stats = Stats()

    for src_name, dst_name in SECTIONS_TO_KEEP.items():
        src_section = src_dir / src_name
        if not src_section.is_dir():
            logger.warning("section %s not found in %s, skipping", src_name, src_dir)
            continue

        dst_section = dst_dir / dst_name
        logger.info("processing section %s -> %s", src_name, dst_name)
        process_section(src_section, dst_section, src_dir, stats, dry_run=dry_run)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-process Go pipeline manpage output: "
        "copy sections, rename directories, fix symlinks.",
    )
    parser.add_argument(
        "src",
        help="Source directory (Go pipeline output, e.g. output/manpages.gz/26.04)",
    )
    parser.add_argument(
        "dst",
        help="Destination directory (e.g. manpages/ubuntu/26.04)",
    )
    parser.add_argument(
        "--log",
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing anything",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO))

    src_dir = Path(args.src)
    dst_dir = Path(args.dst)

    if not src_dir.is_dir():
        parser.error(f"source directory does not exist: {src_dir}")

    stats = postprocess(src_dir, dst_dir, dry_run=args.dry_run)

    prefix = "(dry run) " if args.dry_run else ""
    logger.info(
        "%sDone: %d files copied, %d symlinks copied, %d symlinks rewritten, "
        "%d symlinks skipped (broken), %d directories skipped",
        prefix,
        stats.files_copied,
        stats.symlinks_copied,
        stats.symlinks_rewritten,
        stats.symlinks_skipped,
        stats.dirs_skipped,
    )


if __name__ == "__main__":
    main()
