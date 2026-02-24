import unittest

from explainshell.web.views import explain_program
from tests import helpers


class TestExplainProgram(unittest.TestCase):
    def setUp(self):
        self.store = helpers.MockStore()

    def test_explain_program_returns_str_options(self):
        mp, suggestions = explain_program("bar", self.store)
        self.assertEqual(mp["program"], "bar(1)")
        self.assertEqual(mp["synopsis"], "bar synopsis")
        self.assertEqual(mp["section"], "1")
        self.assertEqual(mp["source"], "bar.1")
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
