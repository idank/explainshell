"""Source-based man page option extractor.

Extracts options directly from the roff source of man pages.
"""

from __future__ import annotations

import gzip
import logging
import os

from explainshell import errors, roff_parser
from explainshell.extraction.common import build_manpage_metadata, build_raw_manpage
from explainshell.extraction.postprocess import postprocess
from explainshell.extraction.types import ExtractionResult, ExtractionStats

logger = logging.getLogger(__name__)


class SourceExtractor:
    """Extracts options from roff source via roff_parser."""

    def extract(self, gz_path: str) -> ExtractionResult:
        options = roff_parser.parse_options(gz_path)
        if not options:
            raise errors.ExtractionError(
                f"roff parser found no options in {os.path.basename(gz_path)}"
            )

        logger.info("roff parser extracted %d options from %s", len(options), gz_path)

        options, pp_stats = postprocess(options, steps=["sanitize", "strip_blanks"])

        with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
            roff_text = f.read()

        mp = build_manpage_metadata(
            gz_path,
            options,
            extractor="source",
            extraction_meta={},
        )

        raw = build_raw_manpage(roff_text, "roff", gz_path)

        stats = ExtractionStats(
            malformed_options=pp_stats.malformed_options,
        )

        return ExtractionResult(mp=mp, raw=raw, stats=stats)
