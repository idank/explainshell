"""Tests for explainshell.source_extractor."""

import unittest
from unittest.mock import patch

from explainshell import store
from explainshell.source_extractor import ExtractionError, extract


class TestExtract(unittest.TestCase):
    @patch("explainshell.source_extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.source_extractor.roff_parser.parse_options")
    def test_returns_manpage(self, mock_roff, mock_synopsis):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        fake_opts = [
            store.Option(
                store.Paragraph(0, "Do not output trailing newline.", "OPTIONS", True),
                ["-n"], [], False, None, False,
            ),
        ]
        mock_roff.return_value = fake_opts

        mp = extract("dummy.1.gz")

        self.assertIsInstance(mp, store.ManPage)
        self.assertEqual(mp.name, "dummy")
        self.assertEqual(mp.synopsis, "a test tool")
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-n"])

    @patch("explainshell.source_extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.source_extractor.roff_parser.parse_options")
    def test_raises_when_no_options(self, mock_roff, mock_synopsis):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_roff.return_value = []

        with self.assertRaises(ExtractionError):
            extract("dummy.1.gz")


if __name__ == "__main__":
    unittest.main()
