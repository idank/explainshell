"""Shared metadata assembly for all extractors."""

from __future__ import annotations

import datetime
import functools
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from explainshell import config, manpage, models, roff_utils

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRACKED_MANDOC = (_REPO_ROOT / "tools" / "mandoc-md").resolve()


def build_manpage_metadata(
    gz_path: str,
    options: list[models.Option],
    *,
    dashless_opts: bool = False,
    subcommands: list[str] | None = None,
    extractor: str | None = None,
    extraction_meta: models.ExtractionMeta | None = None,
) -> models.ParsedManpage:
    """One place to assemble synopsis, aliases, and detection metadata."""
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)
    return models.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=dashless_opts,
        nested_cmd=roff_utils.detect_nested_cmd(gz_path),
        subcommands=subcommands or [],
        extractor=extractor,
        extraction_meta=extraction_meta,
    )


def build_raw_manpage(
    mandoc_path: str,
    source_text: str,
    generator: str,
    gz_path: str,
) -> models.RawManpage:
    """One place to build RawManpage with SHA256.

    ``mandoc_path`` identifies the mandoc binary used for the extraction
    and is resolved into ``generator_version``.
    """
    return models.RawManpage(
        source_text=source_text,
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        generator=generator,
        generator_version=resolve_mandoc_version(mandoc_path),
        source_gz_sha256=gz_sha256(gz_path),
    )


def gz_sha256(gz_path: str) -> str:
    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_sha256_prefix(path: str, length: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


def _git_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@functools.lru_cache(maxsize=8)
def _resolve_mandoc_version_cached(
    abs_path: str, _mtime_size: tuple[float, int]
) -> str:
    if Path(abs_path) == _TRACKED_MANDOC:
        # `git diff --quiet` returns 1 when there are working-tree changes.
        dirty = _git_run(["diff", "--quiet", "HEAD", "--", "tools/mandoc-md"])
        if dirty.returncode == 0:
            log = _git_run(["log", "-1", "--format=%h", "--", "tools/mandoc-md"])
            sha = log.stdout.strip()
            if sha:
                return f"repo:{sha}"
        return f"repo:dirty:{_file_sha256_prefix(abs_path)}"
    return f"custom:{_file_sha256_prefix(abs_path)}"


def resolve_mandoc_version(binary_path: str) -> str | None:
    """Identify the mandoc binary that produced an extraction.

    Returns one of:
      ``repo:<short-sha>``         tracked tools/mandoc-md, clean working tree.
      ``repo:dirty:<sha256-pfx>``  tools/mandoc-md but modified vs HEAD.
      ``custom:<sha256-pfx>``      any other binary (system mandoc, env override).
      ``None``                     binary cannot be located.
    """
    located = shutil.which(binary_path) or binary_path
    try:
        abs_path = str(Path(located).resolve(strict=True))
    except (OSError, RuntimeError):
        return None
    st = os.stat(abs_path)
    return _resolve_mandoc_version_cached(abs_path, (st.st_mtime, st.st_size))
