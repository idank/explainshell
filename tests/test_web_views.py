import unittest

from explainshell.web.views import explain_program, manpage_url
from tests import helpers


class TestManpageUrl(unittest.TestCase):
    def test_matching_prefix(self):
        url = manpage_url("ubuntu/25.10/1/tar.1.gz")
        self.assertEqual(
            url,
            "https://manpages.ubuntu.com/manpages/plucky/en/man1/tar.1.html",
        )

    def test_no_match(self):
        self.assertIsNone(manpage_url("custom.1.gz"))

    def test_section_8(self):
        url = manpage_url("ubuntu/25.10/8/iptables.8.gz")
        self.assertEqual(
            url,
            "https://manpages.ubuntu.com/manpages/plucky/en/man8/iptables.8.html",
        )


class TestExplainProgram(unittest.TestCase):
    def setUp(self):
        self.store = helpers.MockStore()

    def test_explain_program_returns_str_options(self):
        mp, suggestions = explain_program("bar", self.store)
        self.assertEqual(mp["program"], "bar(1)")
        self.assertEqual(mp["synopsis"], "bar synopsis")
        self.assertEqual(mp["section"], "1")
        self.assertEqual(mp["source"], "bar.1")
        self.assertEqual(
            mp["url"],
            "https://manpages.ubuntu.com/manpages/plucky/en/man1/bar.1.html",
        )
        for opt in mp["options"]:
            self.assertIsInstance(opt, str)

    def test_explain_program_no_synopsis(self):
        mp, suggestions = explain_program("nosynopsis", self.store)
        self.assertIsNone(mp["synopsis"])

    def test_explain_program_with_suggestions(self):
        mp, suggestions = explain_program("dup", self.store)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["text"], "dup(2)")
        self.assertEqual(suggestions[0]["link"], "2/dup")
