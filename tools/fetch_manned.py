#!/usr/bin/env python3
"""
Fetch man pages from the manned.org weekly database dump.

Downloads the dump files from dl.manned.org, filters for English locale
man pages from a specific distro, and extracts matching man pages as .gz files
compatible with the existing explainshell pipeline.

Usage:
    # First, download the dump (one time, ~16GB):
    python tools/fetch_manned.py download [--dump-url URL] [--data-dir data/manned]

    # Then extract man pages (can be re-run with different options):
    python tools/fetch_manned.py extract [--data-dir data/manned] [--output-dir manpages] \
        [--distro ubuntu] [--release 24.04] [--sections 1,5,8]

Output is written to <output-dir>/<distro>/<release>/<section>/.
"""

import argparse
import csv
import gzip
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_version(version_str):
    """Parse a version string into a tuple of integers for comparison."""
    parts = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


DEFAULT_DUMP_BASE = "https://dl.manned.org"
DEFAULT_DATA_DIR = "ignore/manned"

ALL_FILES = [
    "systems.tsv.zst",
    "mans.tsv.zst",
    "locales.tsv.zst",
    "files.tsv.zst",
    "packages.tsv.zst",
    "package_versions.tsv.zst",
    "contents.tsv.zst",
]


def resolve_dump_url(base_url):
    """Resolve the current dump URL by reading the 'current' pointer."""
    result = subprocess.run(
        ["curl", "-sfL", f"{base_url}/current"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch {base_url}/current")
    date = result.stdout.strip()
    url = f"{base_url}/{date}"
    logger.info("Using dump: %s", url)
    return url


def download_file(url, dest_path):
    """Download a file with curl, skipping if it already exists."""
    if os.path.exists(dest_path):
        logger.info("Already exists, skipping: %s", dest_path)
        return
    logger.info("Downloading %s -> %s", url, dest_path)
    tmp_path = dest_path + ".tmp"
    result = subprocess.run(
        ["curl", "-fL", "-o", tmp_path, url],
    )
    if result.returncode != 0:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to download {url}")
    os.rename(tmp_path, dest_path)
    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    logger.info("  -> %.1f MB", size_mb)


def decompress_zst(zst_path):
    """Decompress a .zst file, returning the path to the decompressed file."""
    tsv_path = zst_path.removesuffix(".zst")
    if os.path.exists(tsv_path):
        logger.info("Already decompressed, skipping: %s", tsv_path)
        return tsv_path
    logger.info("Decompressing %s", zst_path)
    result = subprocess.run(["zstd", "-d", "-k", zst_path])
    if result.returncode != 0:
        raise RuntimeError(f"Failed to decompress {zst_path}")
    return tsv_path


def parse_tsv(path):
    """Parse a TSV file, yielding rows as tuples."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        yield from reader


def cmd_download(args):
    """Download all dump files from manned.org."""
    for cmd in ["curl", "zstd"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            logger.error("Required command '%s' not found. Please install it.", cmd)
            sys.exit(1)

    dump_url = args.dump_url or resolve_dump_url(DEFAULT_DUMP_BASE)
    data_dir = args.data_dir
    os.makedirs(data_dir, exist_ok=True)

    for fname in ALL_FILES:
        dest = os.path.join(data_dir, fname)
        download_file(f"{dump_url}/{fname}", dest)

    logger.info("=== Download complete. Files saved to %s ===", data_dir)


def cmd_extract(args):
    """Extract man pages from the downloaded dump files."""
    for cmd in ["zstd"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            logger.error("Required command '%s' not found. Please install it.", cmd)
            sys.exit(1)

    data_dir = args.data_dir
    distro = args.distro.lower()

    # Check that dump files exist
    for fname in ALL_FILES:
        zst_path = os.path.join(data_dir, fname)
        if not os.path.exists(zst_path):
            logger.error("Missing dump file: %s. Run 'download' first.", zst_path)
            sys.exit(1)

    # Decompress metadata files (small ones, skip contents)
    metadata_files = [f for f in ALL_FILES if f != "contents.tsv.zst"]
    for fname in metadata_files:
        decompress_zst(os.path.join(data_dir, fname))

    # Parse sections filter
    sections = None
    if args.sections:
        sections = set(args.sections.split(","))
        logger.info("Filtering to sections: %s", sections)

    # Load metadata
    logger.info("=== Loading metadata ===")
    systems, english_locale_ids, mans, packages, pkg_versions = load_metadata(data_dir)

    # Find matching system IDs for the requested distro
    distro_systems = {}  # sys_id -> (name, release, short)
    for sys_id, (name, release, short) in systems.items():
        if distro in name.lower():
            distro_systems[sys_id] = (name, release, short)
    if not distro_systems:
        available = sorted(set(name.lower() for name, _, _ in systems.values()))
        logger.error(
            "No systems found matching distro '%s'. Available: %s", distro, available
        )
        sys.exit(1)

    # Filter by release
    release = args.release
    if release == "latest":
        # Pick the latest release by sorting version strings
        releases = set(r for _, r, _ in distro_systems.values() if r)
        if not releases:
            # Rolling-release distro (e.g. Arch Linux) — no versioned releases.
            # Use "latest" as a synthetic release label so the on-disk layout
            # keeps the distro/release/section/file.gz convention expected by
            # source_from_path().
            release = "latest"
            logger.info("Rolling-release distro, using 'latest' as release label")
        else:
            release = sorted(releases, key=parse_version)[-1]
            logger.info("Auto-selected latest release: %s", release)

    matching_sys_ids = set()
    match_release = "" if release == "latest" else release
    for sys_id, (name, r, short) in distro_systems.items():
        if r == match_release:
            matching_sys_ids.add(sys_id)
    if not matching_sys_ids:
        available = sorted(set(r for _, r, _ in distro_systems.values() if r))
        logger.error(
            "No systems found for release '%s'. Available releases: %s",
            release,
            available,
        )
        sys.exit(1)
    logger.info(
        "Matched %d system entries for distro '%s' release '%s'",
        len(matching_sys_ids),
        distro,
        release,
    )

    # Select content IDs
    logger.info("=== Selecting man pages ===")
    content_to_manpages = select_content_ids(
        data_dir,
        sections,
        english_locale_ids,
        mans,
        packages,
        pkg_versions,
        matching_sys_ids,
    )

    # Extract from contents
    logger.info("=== Extracting content ===")
    output_dir = os.path.join(os.path.abspath(args.output_dir), distro, release)
    os.makedirs(output_dir, exist_ok=True)
    extract_contents(data_dir, content_to_manpages, output_dir)

    # Resolve .so redirects — manned.org stores roff-level .so directives as
    # regular file content rather than filesystem symlinks.  Replace them with
    # symlinks so the extraction pipeline can map aliases correctly.
    logger.info("=== Resolving .so redirects ===")
    so_stats = resolve_so_redirects(Path(output_dir))
    logger.info(
        ".so redirects: %d found, %d resolved, %d unresolvable",
        so_stats["found"],
        so_stats["resolved"],
        so_stats["unresolvable"],
    )

    logger.info("=== Done! Man pages written to %s ===", output_dir)


# ---------------------------------------------------------------------------
# .so redirect resolution
# ---------------------------------------------------------------------------

# Matches ".so man1/foo.1" or ".so foo.1" (with optional trailing whitespace).
_SO_RE = re.compile(r"^\.so\s+(?P<target>\S+)\s*$")


def _parse_so_redirect(content: str) -> str | None:
    """If *content* is a pure .so redirect, return the target path; else None."""
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    m = _SO_RE.match(lines[0])
    return m.group("target") if m else None


def _resolve_so_target(redirect_path: Path, so_target: str, root: Path) -> Path | None:
    """Resolve a .so target string to an actual .gz file under *root*.

    Handles two formats:
      - "man<N>/<name>.<N>"  → root/<N>/<name>.<N>.gz
      - "<name>.<N>"         → root/<same-section>/<name>.<N>.gz
    """
    m = re.match(r"^man(\d+)/(.+)$", so_target)
    if m:
        section = m.group(1)
        basename = m.group(2)
        candidate = root / section / f"{basename}.gz"
        if candidate.exists():
            return candidate
        # Upstream packaging bugs may embed junk path components
        # (e.g. "man1/./build/man/podman-play.1").  Fall back to the
        # filename only.
        filename = Path(basename).name
        if filename != basename:
            candidate = root / section / f"{filename}.gz"
            if candidate.exists():
                return candidate
        return None

    # Bare filename — same section as the source file.
    candidate = redirect_path.parent / f"{so_target}.gz"
    if candidate.exists():
        return candidate
    return None


def resolve_so_redirects(root: Path) -> dict[str, int]:
    """Replace .so redirect files under *root* with symlinks to their targets.

    Returns a dict with counts: found, resolved, unresolvable.
    """
    # Collect non-symlink .gz files.
    gz_files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if fname.endswith(".gz"):
                p = Path(dirpath) / fname
                if not p.is_symlink():
                    gz_files.append(p)

    # Pass 1: identify redirects and resolve targets.
    redirects: dict[Path, tuple[str, Path]] = {}
    unresolvable = 0
    for gz_path in gz_files:
        try:
            with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue
        so_target = _parse_so_redirect(content)
        if so_target is None:
            continue
        resolved = _resolve_so_target(gz_path, so_target, root)
        if resolved is None:
            unresolvable += 1
            logger.warning(
                "deleting unresolvable .so redirect: %s -> %s", gz_path, so_target
            )
            gz_path.unlink()
            continue
        redirects[gz_path] = (so_target, resolved)

    # Pass 2: follow chains (A→B→C) to the terminal real file.
    resolved_count = 0
    for gz_path, (so_target, target) in redirects.items():
        final = target
        depth = 0
        while final in redirects and depth < 10:
            _, final = redirects[final]
            depth += 1
        if final in redirects:
            unresolvable += 1
            logger.warning(
                "deleting circular .so redirect: %s -> %s", gz_path, so_target
            )
            gz_path.unlink()
            continue
        rel_target = os.path.relpath(final, gz_path.parent)
        gz_path.unlink()
        os.symlink(rel_target, gz_path)
        resolved_count += 1

    return {
        "found": len(redirects) + unresolvable,
        "resolved": resolved_count,
        "unresolvable": unresolvable,
    }


def load_metadata(data_dir):
    """Parse all metadata files. Returns parsed data structures."""
    # Parse systems: id -> (name, release, short)
    systems = {}
    for row in parse_tsv(os.path.join(data_dir, "systems.tsv")):
        sys_id = int(row[0])
        name = row[1]
        release = row[2] if len(row) > 2 else ""
        short = row[3] if len(row) > 3 else ""
        # Normalize PostgreSQL COPY NULL marker
        if release == "\\N":
            release = ""
        if short == "\\N":
            short = ""
        systems[sys_id] = (name, release, short)
    logger.info("Loaded %d systems", len(systems))

    # Parse locales: id -> locale
    locales = {}
    for row in parse_tsv(os.path.join(data_dir, "locales.tsv")):
        loc_id = int(row[0])
        locale = row[1]
        locales[loc_id] = locale
    logger.info("Loaded %d locales", len(locales))

    # Determine English locale IDs
    english_locale_ids = set()
    for loc_id, locale in locales.items():
        if locale == "" or locale.startswith("en"):
            english_locale_ids.add(loc_id)
    logger.info("English locale IDs: %s", english_locale_ids)

    # Parse mans: id -> (name, section)
    mans = {}
    for row in parse_tsv(os.path.join(data_dir, "mans.tsv")):
        man_id = int(row[0])
        name, section = row[1], row[2]
        mans[man_id] = (name, section)
    logger.info("Loaded %d man page entries", len(mans))

    # Parse packages: id -> system_id
    packages = {}
    for row in parse_tsv(os.path.join(data_dir, "packages.tsv")):
        pkg_id = int(row[0])
        system = int(row[1])
        packages[pkg_id] = system
    logger.info("Loaded %d packages", len(packages))

    # Parse package_versions: id -> package_id
    pkg_versions = {}
    for row in parse_tsv(os.path.join(data_dir, "package_versions.tsv")):
        pv_id = int(row[0])
        pkg_id = int(row[1])
        pkg_versions[pv_id] = pkg_id
    logger.info("Loaded %d package versions", len(pkg_versions))

    return systems, english_locale_ids, mans, packages, pkg_versions


def _is_standard_manpath(filename):
    """Check if filename is under a standard man page directory.

    Matches manned.org's is_standard_man_location() logic. Prefers man pages
    installed to standard paths over application-specific directories (e.g.
    fish, zsh).
    """
    return filename.startswith("/usr/share/man/man") or filename.startswith(
        "/usr/local/man/man"
    )


def select_content_ids(
    data_dir,
    sections,
    english_locale_ids,
    mans,
    packages,
    pkg_versions,
    matching_sys_ids,
):
    """
    Process the files table to select which content IDs to extract.

    Filters by distro and English locale. For each unique man page
    (name+section), picks one content entry, preferring entries from
    standard man page paths (e.g. /usr/share/man/).

    Returns:
        content_to_manpages: dict mapping content_id -> [(name, section), ...]
    """
    # Track seen (name, section) -> (content_id, is_standard) to deduplicate
    seen = {}

    files_path = os.path.join(data_dir, "files.tsv")
    count = 0
    skipped_locale = 0
    skipped_section = 0
    skipped_distro = 0

    for row in parse_tsv(files_path):
        # files: pkgver, man, content, shorthash, locale, encoding, filename
        pkgver_id = int(row[0])
        man_id = int(row[1])
        content_id = int(row[2])
        locale_id = int(row[4])
        filename = row[6] if len(row) > 6 else ""
        count += 1

        # Filter: English locale only
        if locale_id not in english_locale_ids:
            skipped_locale += 1
            continue

        # Filter: matching distro only
        pkg_id = pkg_versions.get(pkgver_id)
        if pkg_id is None:
            continue
        sys_id = packages.get(pkg_id)
        if sys_id is None:
            continue
        if sys_id not in matching_sys_ids:
            skipped_distro += 1
            continue

        # Look up man page name and section
        if man_id not in mans:
            continue
        name, section = mans[man_id]

        # Skip non-standard sections (must start with a digit, e.g. 1, 3p, 5ssl)
        if not section or not section[0].isdigit():
            skipped_section += 1
            continue

        # Filter by requested sections
        if sections and section not in sections:
            skipped_section += 1
            continue

        key = (name, section)
        is_standard = _is_standard_manpath(filename)
        prev = seen.get(key)
        if prev is None or (is_standard and not prev[1]):
            seen[key] = (content_id, is_standard)

    logger.info(
        "Processed %d file entries: %d skipped (locale), %d skipped (distro), "
        "%d skipped (section), %d unique man pages selected",
        count,
        skipped_locale,
        skipped_distro,
        skipped_section,
        len(seen),
    )

    # Build content_id -> [(name, section), ...] mapping
    content_to_manpages = defaultdict(list)
    for (name, section), (content_id, _) in seen.items():
        content_to_manpages[content_id].append((name, section))

    logger.info("Need %d unique content entries", len(content_to_manpages))
    return content_to_manpages


def extract_contents(data_dir, content_to_manpages, output_dir):
    """
    Stream contents.tsv.zst and extract matching man pages as .gz files.

    The contents TSV has columns: id, hash, content
    Streams via zstd to avoid loading the full file into memory.
    """
    contents_zst = os.path.join(data_dir, "contents.tsv.zst")
    logger.info("Streaming %s ...", contents_zst)

    zstd_proc = subprocess.Popen(
        ["zstd", "-d", "-c", contents_zst],
        stdout=subprocess.PIPE,
    )

    sections_created = set()
    extracted = 0
    symlinks_created = 0
    total_needed = len(content_to_manpages)
    remaining = set(content_to_manpages.keys())

    for line_bytes in zstd_proc.stdout:
        if not remaining:
            break

        try:
            line = line_bytes.decode("utf-8", errors="replace")
        except Exception:
            continue

        # Split on first two tabs only (content may contain tabs)
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue

        try:
            content_id = int(parts[0])
        except ValueError:
            continue

        if content_id not in remaining:
            continue

        # Unescape PostgreSQL COPY format
        raw_content = parts[2]
        raw_content = (
            raw_content.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
        )
        # Remove trailing newline from the TSV row itself
        if raw_content.endswith("\n"):
            raw_content = raw_content[:-1]

        # Write out as .gz files; first entry is the real file, rest are symlinks
        pages = content_to_manpages[content_id]
        canonical_name, canonical_section = pages[0]
        canonical_section_dir = os.path.join(output_dir, canonical_section)
        if canonical_section_dir not in sections_created:
            os.makedirs(canonical_section_dir, exist_ok=True)
            sections_created.add(canonical_section_dir)

        canonical_gz = f"{canonical_name}.{canonical_section}.gz"
        canonical_path = os.path.join(canonical_section_dir, canonical_gz)
        with gzip.open(canonical_path, "wt", encoding="utf-8") as gz_file:
            gz_file.write(raw_content)

        for name, section in pages[1:]:
            section_dir = os.path.join(output_dir, section)
            if section_dir not in sections_created:
                os.makedirs(section_dir, exist_ok=True)
                sections_created.add(section_dir)

            gz_filename = f"{name}.{section}.gz"
            gz_path = os.path.join(section_dir, gz_filename)
            target = os.path.relpath(canonical_path, section_dir)
            os.symlink(target, gz_path)
            symlinks_created += 1

        remaining.discard(content_id)
        extracted += 1

        if extracted % 1000 == 0:
            logger.info(
                "  extracted %d / %d content entries (%d remaining)",
                extracted,
                total_needed,
                len(remaining),
            )

    zstd_proc.terminate()
    zstd_proc.wait()

    logger.info(
        "Extraction complete: %d / %d content entries written, %d symlinks created",
        extracted,
        total_needed,
        symlinks_created,
    )
    if remaining:
        logger.warning("%d content IDs were not found in the dump", len(remaining))


def main():
    parser = argparse.ArgumentParser(
        description="Fetch man pages from manned.org database dump"
    )
    parser.add_argument(
        "--log",
        default="INFO",
        help="Log level (default: INFO)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download subcommand
    dl_parser = subparsers.add_parser(
        "download",
        help="Download dump files from manned.org",
    )
    dl_parser.add_argument(
        "--dump-url",
        default=None,
        help="Full URL to dump directory (e.g. https://dl.manned.org/2026-02-21). "
        "Auto-resolves from 'current' pointer if not specified.",
    )
    dl_parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory to store dump files (default: {DEFAULT_DATA_DIR})",
    )

    # extract subcommand
    ex_parser = subparsers.add_parser(
        "extract",
        help="Extract man pages from downloaded dump files",
    )
    ex_parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory containing dump files (default: {DEFAULT_DATA_DIR})",
    )
    ex_parser.add_argument(
        "--output-dir",
        "-o",
        default="manpages",
        help="Output directory for .gz man page files (default: manpages)",
    )
    ex_parser.add_argument(
        "--distro",
        "-d",
        default="ubuntu",
        help="Distribution to extract man pages from (default: ubuntu). "
        "Matched case-insensitively against system names.",
    )
    ex_parser.add_argument(
        "--release",
        "-r",
        default="latest",
        help="Distribution release version to extract (e.g. '24.04'). "
        "Default: 'latest' (auto-selects the newest release).",
    )
    ex_parser.add_argument(
        "--sections",
        "-s",
        default=None,
        help="Comma-separated list of man page sections to fetch (e.g. 1,5,8). "
        "Default: all sections.",
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "download":
        cmd_download(args)
    elif args.command == "extract":
        cmd_extract(args)


if __name__ == "__main__":
    main()
