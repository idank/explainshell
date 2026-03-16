"""Mandoc-based man page option extractor.

Uses mandoc -T tree parsing to extract options from man pages.
"""

from __future__ import annotations

import logging
import os

from explainshell import errors, tree_parser
from explainshell.extraction.common import build_manpage_metadata, build_raw_manpage
from explainshell.extraction.postprocess import postprocess
from explainshell.extraction.types import ExtractionResult, ExtractionStats

logger = logging.getLogger(__name__)


class MandocExtractor:
    """Extracts options using the mandoc -T tree parser.

    Raises LowConfidenceError when confidence is low. This is a subclass
    of ExtractionError, so runners treat it as FAILED. HybridExtractor
    catches it specifically to trigger LLM fallback.
    """

    def extract(self, gz_path: str) -> ExtractionResult:
        result = tree_parser.parse_options(gz_path)
        if not result.options:
            raise errors.ExtractionError(
                f"tree parser found no options in {os.path.basename(gz_path)}"
            )

        logger.info(
            "tree parser extracted %d options from %s", len(result.options), gz_path
        )

        options, pp_stats = postprocess(
            result.options, steps=["sanitize", "strip_blanks"]
        )

        mp = build_manpage_metadata(
            gz_path,
            options,
            extractor="mandoc",
            extraction_meta={},
        )

        confidence = tree_parser.assess_confidence(result)
        if not confidence.confident:
            raise errors.LowConfidenceError(str(confidence), manpage=mp)

        raw = build_raw_manpage(result.tree_text, "mandoc -T tree", gz_path)

        stats = ExtractionStats(
            dropped_empty=pp_stats.dropped_empty,
        )

        return ExtractionResult(mp=mp, raw=raw, stats=stats)
