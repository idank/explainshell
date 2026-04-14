import logging
import os
import unittest
from unittest.mock import patch

from explainshell.logger.logging_interceptor import InterceptHandler
from explainshell.web import create_app


class TestCreateAppConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("explainshell")
        self.original_handlers = list(self.logger.handlers)
        self.original_level = self.logger.level
        self.original_propagate = self.logger.propagate

    def tearDown(self) -> None:
        self.logger.handlers = self.original_handlers
        self.logger.setLevel(self.original_level)
        self.logger.propagate = self.original_propagate

    def test_create_app_reads_db_path_from_env_at_call_time(self) -> None:
        with patch.dict(os.environ, {"DB_PATH": "/tmp/from-env.db"}, clear=False):
            app = create_app()

        self.assertEqual(app.config["DB_PATH"], "/tmp/from-env.db")

    def test_create_app_reads_debug_from_env_at_call_time(self) -> None:
        with patch.dict(os.environ, {"DEBUG": "false"}, clear=False):
            app = create_app()

        self.assertNotIn("manpage.show_manpage", app.view_functions)
        self.assertFalse(app.config["DEBUG"])

    def test_create_app_configures_explainshell_logging_once(self) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "ERROR"}, clear=False):
            create_app()
            create_app(log_level="WARNING")

        handlers = [
            handler
            for handler in self.logger.handlers
            if isinstance(handler, InterceptHandler)
        ]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(self.logger.level, logging.WARNING)
        self.assertFalse(self.logger.propagate)
