"""
Mandoc-based man page option extractor.

Uses mandoc -T tree parsing to extract options from man pages.

Public API:
    extract(gz_path) -> (store.ParsedManpage, store.RawManpage)
    build_manpage(gz_path, options) -> store.ParsedManpage
"""

import datetime
import hashlib
import logging
import os

from explainshell import config, errors, manpage, roff_utils, store, tree_parser

logger = logging.getLogger(__name__)


def build_manpage(gz_path: str, options: list) -> store.ParsedManpage:
    """Build a ParsedManpage from pre-extracted options.

    Wraps the options with synopsis, aliases, dashless_opts, and nested_cmd
    detection. Used by both extract() and the manager's hybrid logic.
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)
    return store.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=roff_utils.detect_dashless_opts(gz_path),
        nested_cmd=roff_utils.detect_nested_cmd(gz_path),
    )


def _gz_sha256(gz_path):
    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(gz_path: str) -> tuple[store.ParsedManpage, store.RawManpage]:
    """Extract options using the mandoc -T tree parser.

    Raises errors.ExtractionError if the tree parser finds no options.
    Raises errors.LowConfidenceError if the result has low confidence
    (the partial manpage is attached to the exception).

    Returns (ParsedManpage, RawManpage).
    """
    result = tree_parser.parse_options(gz_path)
    if not result.options:
        raise errors.ExtractionError(
            f"tree parser found no options in {os.path.basename(gz_path)}"
        )

    logger.info(
        "tree parser extracted %d options from %s", len(result.options), gz_path
    )
    mp = build_manpage(gz_path, result.options)

    confidence = tree_parser.assess_confidence(result)
    if not confidence.confident:
        raise errors.LowConfidenceError(str(confidence), manpage=mp)

    raw = store.RawManpage(
        source_text=result.tree_text,
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        generator="mandoc -T tree",
        source_gz_sha256=_gz_sha256(gz_path),
    )

    return mp, raw
