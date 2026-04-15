"""Tests for explainshell.extraction.source.SourceExtractor."""

import os
import unittest
from unittest.mock import patch

from tests.helpers import TESTS_DIR

from explainshell import models
from explainshell.errors import ExtractionError
from explainshell.extraction.source import SourceExtractor
from explainshell.roff_utils import detect_dashless_opts, detect_nested_cmd

_MANPAGES = os.path.join(TESTS_DIR, "manpages", "ubuntu", "26.04", "1")
_MANPAGES_CUSTOM = os.path.join(TESTS_DIR, "manpages", "ubuntu", "26.04", "1")


def _gz(name: str, custom: bool = False) -> str:
    base = _MANPAGES_CUSTOM if custom else _MANPAGES
    return os.path.join(base, name)


class TestExtract(unittest.TestCase):
    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch(
        "explainshell.extraction.common.roff_utils.detect_dashless_opts",
        return_value=False,
    )
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.source.roff_parser.parse_options")
    @patch("explainshell.extraction.source.gzip.open")
    def test_returns_manpage(
        self, mock_gzip, mock_roff, mock_synopsis, mock_sha, mock_dashless, mock_nested
    ):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        fake_opts = [
            models.Option(
                text="Do not output trailing newline.",
                short=["-n"],
                long=[],
                has_argument=False,
            ),
        ]
        mock_roff.return_value = fake_opts
        mock_gzip.return_value.__enter__ = lambda s: __import__("io").StringIO(
            ".TH DUMMY 1\nfake roff"
        )
        mock_gzip.return_value.__exit__ = lambda s, *a: None

        gz_path = _gz("dummy.1.gz")
        ext = SourceExtractor()
        result = ext.extract(gz_path)

        self.assertIsInstance(result.mp, models.ParsedManpage)
        self.assertEqual(result.mp.synopsis, "a test tool")
        self.assertEqual(len(result.mp.options), 1)
        self.assertEqual(result.mp.options[0].short, ["-n"])
        self.assertFalse(result.mp.dashless_opts)

    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.source.roff_parser.parse_options")
    def test_raises_when_no_options(self, mock_roff, mock_synopsis):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_roff.return_value = []

        gz_path = _gz("dummy.1.gz")
        ext = SourceExtractor()
        with self.assertRaises(ExtractionError):
            ext.extract(gz_path)


class TestDetectDashlessOpts(unittest.TestCase):
    def test_tar_detected(self):
        """tar has .SS Traditional usage in SYNOPSIS."""
        self.assertTrue(detect_dashless_opts(_gz("tar.1.gz")))

    def test_ps_detected(self):
        """ps mentions BSD options without dash in DESCRIPTION."""
        self.assertTrue(detect_dashless_opts(_gz("ps.1.gz")))

    def test_grep_not_detected(self):
        """grep is a normal command with no dashless options."""
        self.assertFalse(detect_dashless_opts(_gz("grep.1.gz")))

    def test_extract_sets_dashless_opts_for_tar(self):
        """extract() wires detect_dashless_opts into the result."""
        ext = SourceExtractor()
        result = ext.extract(_gz("tar.1.gz"))
        self.assertTrue(result.mp.dashless_opts)


class TestDetectNestedCmd(unittest.TestCase):
    def test_watch_detected(self):
        """watch has 'command' in synopsis."""
        self.assertTrue(detect_nested_cmd(_gz("watch.1.gz")))

    def test_xargs_detected(self):
        """xargs has [command [initial-arguments]] in synopsis."""
        self.assertTrue(detect_nested_cmd(_gz("xargs.1.gz")))

    def test_doas_detected(self):
        """doas has .Ar command in synopsis."""
        self.assertTrue(detect_nested_cmd(_gz("doas.1.gz")))

    def test_curl_not_detected(self):
        """curl has no 'command' in synopsis."""
        self.assertFalse(detect_nested_cmd(_gz("curl.1.gz")))

    def test_git_not_detected(self):
        """git has <command> (angle brackets), should be excluded."""
        self.assertFalse(detect_nested_cmd(_gz("git.1.gz")))

    def test_extract_sets_nested_cmd(self):
        """extract() wires detect_nested_cmd into the result."""
        ext = SourceExtractor()
        result = ext.extract(_gz("watch.1.gz"))
        self.assertTrue(result.mp.nested_cmd)


if __name__ == "__main__":
    unittest.main()
