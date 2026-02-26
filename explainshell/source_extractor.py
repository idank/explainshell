"""
Source-based man page option extractor.

Extracts options directly from the roff source of man pages,
without using an LLM.

Public API:
    extract(gz_path) -> store.ParsedManpage
"""

import logging
import os

from explainshell import config, errors, manpage, roff_parser, store
from explainshell.roff_utils import detect_dashless_opts, detect_nested_cmd

logger = logging.getLogger(__name__)


def extract(gz_path: str) -> store.ParsedManpage:
    """Extract options from raw roff source.

    Raises errors.ExtractionError if the roff parser finds no options.
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)

    options = roff_parser.parse_options(gz_path)
    if not options:
        raise errors.ExtractionError(
            f"roff parser found no options in {os.path.basename(gz_path)}"
        )

    logger.info(
        "roff parser extracted %d options from %s", len(options), gz_path
    )
    return store.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=detect_dashless_opts(gz_path),
        nested_cmd=detect_nested_cmd(gz_path),
    )
