"""
Source-based man page option extractor.

Extracts options directly from the roff source of man pages,
without using an LLM.

Public API:
    extract(gz_path) -> (store.ParsedManpage, store.RawManpage)
"""

import datetime
import gzip
import hashlib
import logging
import os

from explainshell import config, errors, manpage, roff_parser, store
from explainshell.roff_utils import detect_dashless_opts, detect_nested_cmd

logger = logging.getLogger(__name__)


def _gz_sha256(gz_path):
    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(gz_path: str) -> tuple[store.ParsedManpage, store.RawManpage]:
    """Extract options from raw roff source.

    Raises errors.ExtractionError if the roff parser finds no options.

    Returns (ParsedManpage, RawManpage).
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)

    options = roff_parser.parse_options(gz_path)
    if not options:
        raise errors.ExtractionError(
            f"roff parser found no options in {os.path.basename(gz_path)}"
        )

    logger.info("roff parser extracted %d options from %s", len(options), gz_path)

    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
        roff_text = f.read()

    mp = store.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=detect_dashless_opts(gz_path),
        nested_cmd=detect_nested_cmd(gz_path),
    )

    raw = store.RawManpage(
        source_text=roff_text,
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        generator="roff",
        source_gz_sha256=_gz_sha256(gz_path),
    )

    return mp, raw
