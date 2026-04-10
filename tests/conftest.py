from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_manager_log_files(tmp_path):
    """Prevent CLI tests from writing log files into the real logs/ directory.

    The run_dir is redirected to a pytest tmp_path so report/manifest
    writes still work but nothing accumulates in the repo.
    """
    with patch("explainshell.manager._setup_logging", return_value=str(tmp_path)):
        yield
