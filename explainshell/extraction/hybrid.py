"""Hybrid extractor: mandoc with LLM fallback."""

from __future__ import annotations

import logging

from explainshell.errors import LowConfidenceError
from explainshell.extraction.llm.extractor import LLMExtractor
from explainshell.extraction.mandoc import MandocExtractor
from explainshell.extraction.types import ExtractionResult, ExtractorConfig

logger = logging.getLogger(__name__)


class HybridExtractor:
    """Composes MandocExtractor + LLMExtractor with fallback logic.

    Tries mandoc first; on LowConfidenceError, falls back to LLM.
    mp.extractor is set to the actual backend used.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        self._mandoc = MandocExtractor()
        self._llm = LLMExtractor(config)

    def extract(self, gz_path: str) -> ExtractionResult:
        try:
            return self._mandoc.extract(gz_path)
        except LowConfidenceError as e:
            logger.warning("hybrid: falling back to LLM for %s: %s", gz_path, e)
            result = self._llm.extract(gz_path)
            result.stats.fallback_used = True
            result.stats.fallback_reason = str(e)[:256]
            result.mp.extraction_meta = {
                **(result.mp.extraction_meta or {}),
                "fallback": True,
                "fallback_reason": str(e)[:256],
            }
            return result
