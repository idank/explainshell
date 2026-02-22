"""
Source-based man page option extractor.

Extracts options directly from the roff source of man pages,
without using an LLM.

Public API:
    extract(gz_path) -> store.ManPage
"""

import logging
import os

from explainshell import errors, manpage, roff_parser, store

logger = logging.getLogger(__name__)


def extract(gz_path: str) -> store.ManPage:
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
    return store.ManPage(
        source=os.path.basename(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        paragraphs=options,
        aliases=aliases,
    )
