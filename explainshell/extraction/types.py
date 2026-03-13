"""Core data types for the extraction pipeline."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from explainshell.store import ParsedManpage, RawManpage


@dataclass
class ExtractionStats:
    """Extensible bag of metrics. Adding a stat = one new field with a default.

    All additive fields default to zero so that aggregate accumulators
    (``total = ExtractionStats(); total += per_file_stats``) start correctly.
    Per-file extractors set ``chunks = 1`` (or higher) at extraction time.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    chunks: int = 0
    plain_text_len: int = 0
    elapsed_seconds: float = 0.0
    malformed_options: int = 0
    deduped_options: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None

    def __iadd__(self, other: ExtractionStats) -> ExtractionStats:
        """Accumulate numeric fields, OR boolean fields."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.chunks += other.chunks
        self.plain_text_len += other.plain_text_len
        self.elapsed_seconds += other.elapsed_seconds
        self.malformed_options += other.malformed_options
        self.deduped_options += other.deduped_options
        self.fallback_used = self.fallback_used or other.fallback_used
        return self


@dataclass
class ExtractionResult:
    """Returned by ``Extractor.extract()`` on success.

    Always fully populated -- no optional fields.
    """

    mp: ParsedManpage
    raw: RawManpage
    stats: ExtractionStats


class FileOutcome(enum.Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class FileEntry:
    """Per-file record in a ``BatchResult``.

    For SUCCESS, ``result`` is set and ``result.stats`` has the full metrics.
    For SKIPPED/FAILED, ``error`` describes why. ``stats`` is populated when
    preparation succeeded (even if extraction itself was skipped/failed), so
    bench tools can report ``plain_text_len`` and ``chunks`` for non-success
    files.
    """

    gz_path: str
    outcome: FileOutcome
    result: ExtractionResult | None = None
    stats: ExtractionStats | None = None
    error: str | None = None


@dataclass
class BatchResult:
    """Aggregated result from running an extractor over multiple files.

    ``stats`` accumulates only SUCCESS outcomes.  In batch mode, per-file
    token breakdowns are not available from provider APIs, so
    ``stats.input_tokens`` / ``stats.output_tokens`` will be zero.
    """

    files: list[FileEntry] = field(default_factory=list)
    stats: ExtractionStats = field(default_factory=ExtractionStats)

    @property
    def succeeded(self) -> dict[str, ExtractionResult]:
        return {
            f.gz_path: f.result
            for f in self.files
            if f.outcome == FileOutcome.SUCCESS and f.result is not None
        }

    @property
    def skipped(self) -> list[FileEntry]:
        return [f for f in self.files if f.outcome == FileOutcome.SKIPPED]

    @property
    def failed(self) -> list[FileEntry]:
        return [f for f in self.files if f.outcome == FileOutcome.FAILED]


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
