class ProgramDoesNotExist(Exception):
    pass


class DuplicateManpage(Exception):
    pass


class InvalidSourcePath(Exception):
    pass


class ExtractionError(Exception):
    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        #: The raw LLM response text that caused the error.  Populated only
        #: by the LLM extractor (parse/validation failures); ``None`` for
        #: errors raised by other extractors or generic callers.
        self.raw_response = raw_response


class SkippedExtraction(ExtractionError):
    """File was intentionally skipped (not a failure)."""

    def __init__(self, reason: str, stats: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stats = stats


class FatalExtractionError(ExtractionError):
    """Unrecoverable error that should abort the entire run."""

    pass


class LowConfidenceError(ExtractionError):
    """Extractor produced a result but with low confidence."""

    def __init__(self, message, manpage=None):
        super().__init__(message)
        self.manpage = manpage
