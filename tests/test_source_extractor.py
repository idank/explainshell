"""Tests for explainshell.source_extractor."""

import os
import unittest
from unittest.mock import patch

from explainshell import config, store
from explainshell.errors import ExtractionError
from explainshell.roff_utils import detect_dashless_opts, detect_nested_cmd
from explainshell.source_extractor import extract

_MANPAGES = os.path.join(
    os.path.dirname(__file__), "..", "manpages", "ubuntu", "25.10", "1"
)
_MANPAGES_CUSTOM = os.path.join(
    os.path.dirname(__file__), "manpages", "ubuntu", "12.04", "1"
)


def _gz(name, custom=False):
    base = _MANPAGES_CUSTOM if custom else _MANPAGES
    return os.path.join(base, name)


class TestExtract(unittest.TestCase):
    @patch("explainshell.source_extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.source_extractor.roff_parser.parse_options")
    @patch("explainshell.source_extractor.detect_dashless_opts")
    def test_returns_manpage(self, mock_detect, mock_roff, mock_synopsis):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        fake_opts = [
            store.Option(
                text="Do not output trailing newline.",
                short=["-n"],
                long=[],
                has_argument=False,
            ),
        ]
        mock_roff.return_value = fake_opts
        mock_detect.return_value = False

        gz_path = os.path.join(
            config.MANPAGES_DIR, "ubuntu", "25.10", "1", "dummy.1.gz"
        )
        mp = extract(gz_path)

        self.assertIsInstance(mp, store.ParsedManpage)
        self.assertEqual(mp.source, "ubuntu/25.10/1/dummy.1.gz")
        self.assertEqual(mp.name, "dummy")
        self.assertEqual(mp.synopsis, "a test tool")
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-n"])
        self.assertFalse(mp.dashless_opts)

    @patch("explainshell.source_extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.source_extractor.roff_parser.parse_options")
    def test_raises_when_no_options(self, mock_roff, mock_synopsis):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_roff.return_value = []

        gz_path = os.path.join(
            config.MANPAGES_DIR, "ubuntu", "25.10", "1", "dummy.1.gz"
        )
        with self.assertRaises(ExtractionError):
            extract(gz_path)


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
        mp = extract(_gz("tar.1.gz"))
        self.assertTrue(mp.dashless_opts)


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
        mp = extract(_gz("watch.1.gz"))
        self.assertTrue(mp.nested_cmd)


if __name__ == "__main__":
    unittest.main()
