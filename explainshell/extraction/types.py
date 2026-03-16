"""Core data types for the extraction pipeline."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from explainshell.extraction.llm import PreparedFile
    from explainshell.extraction.llm.providers import BatchProvider
    from explainshell.store import ParsedManpage, RawManpage


@dataclass
class ExtractionStats:
    """Extensible bag of metrics. Adding a stat = one new field with a default.

    All additive fields default to zero so that aggregate accumulators
    (``total = ExtractionStats(); total += per_file_stats``) start correctly.
    Per-file extractors set ``chunks = 1`` (or higher) at extraction time.
    """

    # LLM input token count (batch-level aggregate).
    input_tokens: int = 0
    # LLM output token count (batch-level aggregate).
    output_tokens: int = 0
    # LLM reasoning/thinking tokens (subset of output).
    reasoning_tokens: int = 0
    # Number of text chunks sent to the LLM for this file.
    chunks: int = 0
    # Character count of the manpage plain text after filtering.
    plain_text_len: int = 0
    # Wall-clock time for extraction.
    elapsed_seconds: float = 0.0
    # Options skipped due to invalid LLM output (e.g. bad has_argument,
    # missing lines). Counted at parse time in llm_option_to_store_option.
    malformed_options: int = 0
    # Options removed by drop_empty in postprocessing because they had no
    # flags (short/long) and no positional name — typically caused by the
    # LLM omitting the flag from its response.
    dropped_empty: int = 0
    # Options removed as duplicates (exact-match or strict-subset) by
    # dedup_options in postprocessing.
    deduped_options: int = 0
    # Whether hybrid mode fell back from mandoc to LLM.
    fallback_used: bool = False
    # Reason for the fallback (e.g. low confidence).
    fallback_reason: str | None = None

    def __iadd__(self, other: ExtractionStats) -> ExtractionStats:
        """Accumulate numeric fields, OR boolean fields."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.chunks += other.chunks
        self.plain_text_len += other.plain_text_len
        self.elapsed_seconds += other.elapsed_seconds
        self.malformed_options += other.malformed_options
        self.dropped_empty += other.dropped_empty
        self.deduped_options += other.deduped_options
        self.fallback_used = self.fallback_used or other.fallback_used
        return self


class ExtractionOutcome(enum.Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ExtractionResult:
    """Per-file extraction result.

    For SUCCESS, ``mp`` and ``raw`` are populated.
    For SKIPPED/FAILED, ``error`` describes why; ``stats`` may still carry
    prep-phase metrics (``plain_text_len``, ``chunks``).

    Extractors return instances with just ``mp``, ``raw``, ``stats`` set;
    the runner fills in ``gz_path`` and ``outcome``.
    """

    mp: ParsedManpage | None = None
    raw: RawManpage | None = None
    stats: ExtractionStats = field(default_factory=ExtractionStats)
    gz_path: str = ""
    outcome: ExtractionOutcome = ExtractionOutcome.SUCCESS
    error: str | None = None


@dataclass
class BatchResult:
    """Aggregated result from running an extractor over multiple files.

    ``stats`` accumulates SUCCESS outcomes plus batch-level token counts.
    In batch mode, token counts are aggregate (not per-file).
    """

    files: list[ExtractionResult] = field(default_factory=list)
    stats: ExtractionStats = field(default_factory=ExtractionStats)

    @property
    def succeeded(self) -> dict[str, ExtractionResult]:
        return {
            f.gz_path: f for f in self.files if f.outcome == ExtractionOutcome.SUCCESS
        }

    @property
    def skipped(self) -> list[ExtractionResult]:
        return [f for f in self.files if f.outcome == ExtractionOutcome.SKIPPED]

    @property
    def failed(self) -> list[ExtractionResult]:
        return [f for f in self.files if f.outcome == ExtractionOutcome.FAILED]


@dataclass(frozen=True)
class ExtractorConfig:
    """Shared configuration for all extractors.

    ``debug_dir``: full prompt/response debug artifacts. The manager only
    populates this in dry-run mode.

    ``fail_dir``: dump raw LLM responses that fail JSON parsing. May be
    set in any mode so failed responses can be inspected after the fact.
    """

    model: str | None = None
    debug_dir: str | None = None
    fail_dir: str | None = None


@runtime_checkable
class Extractor(Protocol):
    def extract(self, gz_path: str) -> ExtractionResult: ...


@runtime_checkable
class BatchExtractor(Extractor, Protocol):
    """Extractor that supports batch execution via a provider API."""

    @property
    def batch_provider(self) -> BatchProvider: ...

    def prepare(self, gz_path: str) -> PreparedFile: ...

    def finalize(
        self, gz_path: str, prepared: PreparedFile, responses: list[str]
    ) -> ExtractionResult: ...
