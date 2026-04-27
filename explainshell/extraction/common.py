"""Shared metadata assembly for all extractors."""

from __future__ import annotations

import datetime
import hashlib

from explainshell import config, manpage, models, roff_utils


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
    source_text: str,
    generator: str,
    gz_path: str,
) -> models.RawManpage:
    """One place to build RawManpage with SHA256."""
    return models.RawManpage(
        source_text=source_text,
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        generator=generator,
        source_gz_sha256=gz_sha256(gz_path),
    )


def gz_sha256(gz_path: str) -> str:
    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
