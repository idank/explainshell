"""Extraction package — public API.

Factory:
    make_extractor(mode, config) -> Extractor

Re-exports:
    ExtractionResult, ExtractionStats, ExtractorConfig, BatchResult,
    FileEntry, FileOutcome, Extractor
"""

from __future__ import annotations

from explainshell.extraction.types import (
    BatchResult,
    ExtractionResult,
    ExtractionStats,
    Extractor,
    ExtractorConfig,
    FileEntry,
    FileOutcome,
)

__all__ = [
    "BatchResult",
    "ExtractionResult",
    "ExtractionStats",
    "Extractor",
    "ExtractorConfig",
    "FileEntry",
    "FileOutcome",
    "make_extractor",
]


def make_extractor(mode: str, config: ExtractorConfig | None = None) -> Extractor:
    config = config or ExtractorConfig()
    if mode == "source":
        from explainshell.extraction.source import SourceExtractor

        return SourceExtractor()
    if mode == "mandoc":
        from explainshell.extraction.mandoc import MandocExtractor

        return MandocExtractor()
    if mode == "llm":
        from explainshell.extraction.llm import LLMExtractor

        return LLMExtractor(config)
    if mode == "hybrid":
        from explainshell.extraction.hybrid import HybridExtractor

        return HybridExtractor(config)
    raise ValueError(f"unknown mode: {mode!r}")
