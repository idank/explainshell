"""Shared metadata assembly for all extractors."""

from __future__ import annotations

import datetime
import hashlib

from explainshell import config, manpage, models, roff_utils


def build_manpage_metadata(
    gz_path: str,
    options: list[models.Option],
    *,
    dashless_opts: bool | None = None,
    nested_cmd: bool | str | None = None,
    extractor: str | None = None,
    extraction_meta: dict | None = None,
) -> models.ParsedManpage:
    """One place to assemble synopsis, aliases, and detection metadata.

    ``dashless_opts`` and ``nested_cmd`` fall back to roff-based detection
    when not supplied.  The LLM extractor passes its own model-derived
    values via these keyword args.
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)
    return models.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=(
            dashless_opts
            if dashless_opts is not None
            else roff_utils.detect_dashless_opts(gz_path)
        ),
        nested_cmd=(
            nested_cmd
            if nested_cmd is not None
            else roff_utils.detect_nested_cmd(gz_path)
        ),
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
