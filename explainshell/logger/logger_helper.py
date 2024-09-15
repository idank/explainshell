"""
This module provides a helper function for logging using the loguru library.

The logger_helper module sets up a logger with a rotated log file and also logs to standard output.
"""

import logging
import sys
from pathlib import Path

import loguru

# intercept log entries handled by python's 'logging' module
# and redirect them to loguru logger
from explainshell.logger.logging_interceptor import InterceptHandler

parent_dir = Path(__file__).parent.parent.parent
logs_dir = parent_dir / "logs"
# create logs directory if it does not exist
logs_dir.mkdir(exist_ok=True)

logger = loguru.logger
logger.remove()


def level_filter():
    """
    Filter function to exclude DEBUG and SUCCESS log levels from being logged to standard output.
    """
    def is_level(record):
        return record["level"].name not in ["DEBUG", "SUCCESS"]
    return is_level


# init rotated log file
logger.add(
    logs_dir / "debug.log",
    rotation="10 MB",
    retention="7 days",
    backtrace=True,
    colorize=False,
    catch=True,
    delay=True,
    diagnose=True,
    enqueue=True,
)


# also log to standard output
logger.add(sys.stdout, colorize=True, filter=level_filter())

# activate logging and redirect all logs to loguru logger
logging.basicConfig(handlers=[InterceptHandler()], level=logging.DEBUG, force=True)
