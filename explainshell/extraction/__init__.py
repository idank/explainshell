"""Extraction package — public API.

Factory:
    make_extractor(mode, config) -> Extractor

Re-exports:
    ExtractionResult, ExtractionStats, ExtractorConfig, BatchResult,
    ExtractionOutcome, Extractor
"""

from __future__ import annotations

from explainshell.extraction.types import (
    BatchResult,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionStats,
    Extractor,
    ExtractorConfig,
)

__all__ = [
    "BatchResult",
    "ExtractionOutcome",
    "ExtractionResult",
    "ExtractionStats",
    "Extractor",
    "ExtractorConfig",
    "make_extractor",
]


def make_extractor(mode: str, config: ExtractorConfig | None = None) -> Extractor:
    config = config or ExtractorConfig()
    if mode == "llm":
        from explainshell.extraction.llm.extractor import LLMExtractor

        return LLMExtractor(config)
    raise ValueError(f"unknown mode: {mode!r}")
