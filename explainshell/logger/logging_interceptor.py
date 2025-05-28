import logging
import sys

import loguru

logger = loguru.logger


class InterceptHandler(logging.Handler):
    """
    intercept log messages logged with the logging module

    source: https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    also see: https://stackoverflow.com/a/70620198
    """

    def emit(self, record):
        # Get corresponding Loguru level if it exists.
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
