import enum


class ProgramDoesNotExist(Exception):
    pass


class DuplicateManpage(Exception):
    pass


class InvalidSourcePath(Exception):
    pass


class FailureReason(str, enum.Enum):
    """Classification of why an extraction skipped or failed.

    Set at the throw site so analysis tools (e.g. report.json consumers)
    don't have to regex error messages.  String-valued so the value lands
    in JSON as a stable identifier.
    """

    # Skips
    BLACKLISTED = "blacklisted"
    MANPAGE_TOO_LARGE = "manpage_too_large"

    # Pre-LLM failures
    TOO_MANY_CHUNKS = "too_many_chunks"
    MANDOC_FAILED = "mandoc_failed"
    CANCELLED = "cancelled"

    # LLM response failures
    INVALID_RESPONSE = "invalid_response"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"

    # Provider failures
    CONTENT_FILTER = "content_filter"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_BATCH_ERROR = "provider_batch_error"

    # Post-processing failures
    LINE_SPAN_COVERAGE = "line_span_coverage"

    # Fallback
    UNEXPECTED = "unexpected"


class ExtractionError(Exception):
    def __init__(
        self,
        message: str,
        raw_response: str | None = None,
        *,
        reason_class: FailureReason | None = None,
    ) -> None:
        super().__init__(message)
        #: The raw LLM response text that caused the error.  Populated only
        #: by the LLM extractor (parse/validation failures); ``None`` for
        #: errors raised by other extractors or generic callers.
        self.raw_response = raw_response
        #: Classification of the failure, set at the throw site so the
        #: runner can propagate it onto ``ExtractionResult.reason_class``
        #: for downstream aggregation.
        self.reason_class = reason_class


class SkippedExtraction(ExtractionError):
    """File was intentionally skipped (not a failure)."""

    def __init__(
        self,
        reason: str,
        stats: object = None,
        *,
        reason_class: FailureReason | None = None,
    ) -> None:
        super().__init__(reason, reason_class=reason_class)
        self.reason = reason
        self.stats = stats


class FatalExtractionError(ExtractionError):
    """Unrecoverable error that should abort the entire run."""
