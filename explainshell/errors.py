class ProgramDoesNotExist(Exception):
    pass


class DuplicateManpage(Exception):
    pass


class ExtractionError(Exception):
    pass


class LowConfidenceError(ExtractionError):
    """Extractor produced a result but with low confidence."""

    def __init__(self, message, manpage=None):
        super().__init__(message)
        self.manpage = manpage
