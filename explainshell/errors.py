class ProgramDoesNotExist(Exception):
    pass


class DuplicateManpage(Exception):
    pass


class InvalidSourcePath(Exception):
    pass


class ExtractionError(Exception):
    pass


class SkippedExtraction(ExtractionError):
    """File was intentionally skipped (not a failure)."""

    def __init__(self, reason: str, stats: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stats = stats


class LowConfidenceError(ExtractionError):
    """Extractor produced a result but with low confidence."""

    def __init__(self, message, manpage=None):
        super().__init__(message)
        self.manpage = manpage
