"""Unit and integration tests for explainshell.llm_ref_extractor."""

import os
import unittest
from unittest.mock import patch

from explainshell import store
from explainshell.llm_ref_extractor import (
    _dedup_ref_options,
    _extract_text_from_lines,
    _number_lines,
    _ref_option_to_store_option,
    extract,
)


# ---------------------------------------------------------------------------
# TestNumberLines
# ---------------------------------------------------------------------------


class TestNumberLines(unittest.TestCase):
    def test_basic(self):
        text = "alpha\nbeta\ngamma"
        numbered, orig = _number_lines(text)
        self.assertEqual(orig[1], "alpha")
        self.assertEqual(orig[2], "beta")
        self.assertEqual(orig[3], "gamma")
        self.assertIn("1| alpha", numbered)
        self.assertIn("2| beta", numbered)
        self.assertIn("3| gamma", numbered)

    def test_single_line(self):
        numbered, orig = _number_lines("hello")
        self.assertEqual(orig[1], "hello")
        self.assertEqual(numbered, "1| hello")

    def test_preserves_empty_lines(self):
        text = "a\n\nb"
        numbered, orig = _number_lines(text)
        self.assertEqual(orig[1], "a")
        self.assertEqual(orig[2], "")
        self.assertEqual(orig[3], "b")

    def test_line_number_padding(self):
        # 100 lines → 3-digit width
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        numbered, orig = _number_lines(text)
        first_line = numbered.split("\n")[0]
        # "  1| line0" — padded to 3 digits
        self.assertTrue(first_line.startswith("  1| "))


# ---------------------------------------------------------------------------
# TestExtractTextFromLines
# ---------------------------------------------------------------------------


class TestExtractTextFromLines(unittest.TestCase):
    def setUp(self):
        self.orig = {
            1: "**-v**, **--verbose**",
            2: "",
            3: "Enable verbose output.",
            4: "Shows detailed information.",
            5: "",
        }

    def test_basic_extraction(self):
        text = _extract_text_from_lines(self.orig, 1, 4)
        self.assertIn("**-v**, **--verbose**", text)
        self.assertIn("Enable verbose output.", text)
        self.assertIn("Shows detailed information.", text)

    def test_flag_line_only(self):
        text = _extract_text_from_lines(self.orig, 1, 1)
        self.assertEqual(text, "**-v**, **--verbose**")

    def test_strips_blockquote_prefix(self):
        orig = {
            1: "> **-n**",
            2: "> ",
            3: "> Do not output trailing newline.",
        }
        text = _extract_text_from_lines(orig, 1, 3)
        self.assertIn("**-n**", text)
        self.assertIn("Do not output trailing newline.", text)
        self.assertNotIn("> ", text)

    def test_invalid_range(self):
        self.assertEqual(_extract_text_from_lines(self.orig, 0, 3), "")
        self.assertEqual(_extract_text_from_lines(self.orig, 5, 3), "")

    def test_skips_leading_blank_body_lines(self):
        text = _extract_text_from_lines(self.orig, 1, 4)
        # Should have flag line, then \n\n, then body — no leading blank line in body
        parts = text.split("\n\n", 1)
        self.assertEqual(len(parts), 2)
        self.assertFalse(parts[1].startswith("\n"))


# ---------------------------------------------------------------------------
# TestRefOptionToStoreOption
# ---------------------------------------------------------------------------


class TestRefOptionToStoreOption(unittest.TestCase):
    def setUp(self):
        self.orig = {
            10: "**-A**, **--catenate**",
            11: "",
            12: "Append files to an archive.",
            13: "This is equivalent to --concatenate.",
        }

    def test_basic(self):
        raw = {
            "short": ["-A"],
            "long": ["--catenate"],
            "expects_arg": False,
            "lines": [10, 13],
        }
        opt = _ref_option_to_store_option(raw, self.orig)
        self.assertIsInstance(opt, store.Option)
        self.assertEqual(opt.short, ["-A"])
        self.assertEqual(opt.long, ["--catenate"])
        self.assertFalse(opt.expects_arg)
        self.assertIn("**-A**, **--catenate**", opt.text)
        self.assertIn("Append files to an archive.", opt.text)

    def test_missing_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "expects_arg": False}
        with self.assertRaises(ValueError):
            _ref_option_to_store_option(raw, self.orig)

    def test_invalid_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "expects_arg": False, "lines": [10]}
        with self.assertRaises(ValueError):
            _ref_option_to_store_option(raw, self.orig)

    def test_bad_short_type_raises(self):
        raw = {"short": "-v", "long": [], "expects_arg": False, "lines": [10, 13]}
        with self.assertRaises(ValueError):
            _ref_option_to_store_option(raw, self.orig)

    def test_nested_cmd_auto_corrects(self):
        raw = {
            "short": [],
            "long": ["--exec"],
            "expects_arg": False,
            "nested_cmd": True,
            "lines": [10, 13],
        }
        opt = _ref_option_to_store_option(raw, self.orig)
        self.assertTrue(opt.nested_cmd)
        self.assertTrue(opt.expects_arg)

    def test_positional_arg(self):
        raw = {
            "short": [],
            "long": [],
            "expects_arg": False,
            "argument": "FILE",
            "lines": [10, 13],
        }
        opt = _ref_option_to_store_option(raw, self.orig)
        self.assertEqual(opt.argument, "FILE")


# ---------------------------------------------------------------------------
# TestDedupRefOptions
# ---------------------------------------------------------------------------


class TestDedupRefOptions(unittest.TestCase):
    def test_dedup_keeps_longest_span(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "lines": [1, 3]},
            {"short": ["-v"], "long": ["--verbose"], "lines": [1, 10]},
        ]
        result = _dedup_ref_options(opts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lines"], [1, 10])


# ---------------------------------------------------------------------------
# TestExtractIntegration
# ---------------------------------------------------------------------------


class TestExtractIntegration(unittest.TestCase):
    @patch("explainshell.llm_ref_extractor._call_llm_ref")
    @patch("explainshell.llm_ref_extractor.get_manpage_text")
    @patch("explainshell.llm_ref_extractor.manpage.get_synopsis_and_aliases")
    def test_extract_returns_manpage(self, mock_synopsis, mock_text, mock_llm):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_text.return_value = "**-n**\n\nDo not output trailing newline.\n\n**-e**\n\nEnable escapes."
        # Lines: 1=**-n**, 2="", 3=Do not..., 4="", 5=**-e**, 6="", 7=Enable...
        mock_llm.return_value = (
            {
                "dashless_opts": False,
                "options": [
                    {"short": ["-n"], "long": [], "expects_arg": False, "lines": [1, 3]},
                    {"short": ["-e"], "long": [], "expects_arg": False, "lines": [5, 7]},
                ],
            },
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
            '{"options": []}',
        )
        mp = extract("dummy.1.gz", "test-model")
        self.assertIsInstance(mp, store.ParsedManpage)
        self.assertEqual(len(mp.options), 2)
        flags = [opt.short[0] for opt in mp.options]
        self.assertIn("-n", flags)
        self.assertIn("-e", flags)
        # Verify text comes from source, not LLM
        n_opt = next(o for o in mp.options if "-n" in o.short)
        self.assertIn("Do not output trailing newline.", n_opt.text)

    @patch("explainshell.llm_ref_extractor._call_llm_ref")
    @patch("explainshell.llm_ref_extractor.get_manpage_text")
    @patch("explainshell.llm_ref_extractor.manpage.get_synopsis_and_aliases")
    def test_malformed_options_skipped(self, mock_synopsis, mock_text, mock_llm):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_text.return_value = "**-v**\n\nVerbose."
        mock_llm.return_value = (
            {
                "options": [
                    {"short": "not-a-list", "long": [], "lines": [1, 3]},
                    {"short": ["-v"], "long": [], "expects_arg": False, "lines": [1, 3]},
                ],
            },
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
            '{"options": []}',
        )
        mp = extract("dummy.1.gz", "test-model")
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-v"])


# ---------------------------------------------------------------------------
# Real-LLM integration test (skipped unless RUN_LLM_TESTS=1)
# ---------------------------------------------------------------------------

_MODEL_KEY_ENV = {
    "gemini/": "GEMINI_API_KEY",
    "gpt-": "OPENAI_API_KEY",
    "o1": "OPENAI_API_KEY",
    "claude-": "ANTHROPIC_API_KEY",
}

_DEFAULT_LLM_MODEL = "gemini/gemini-3-flash-preview"


@unittest.skipUnless(
    os.environ.get("RUN_LLM_TESTS") == "1", "set RUN_LLM_TESTS=1 to run"
)
class TestRealLlmRef(unittest.TestCase):
    ECHO_GZ = os.path.join(os.path.dirname(__file__), "echo.1.gz")

    def setUp(self):
        if not os.path.exists(self.ECHO_GZ):
            import shutil
            src = os.path.join(
                os.path.dirname(__file__),
                "..",
                "manpages",
                "ubuntu",
                "12.04",
                "1",
                "echo.1.gz",
            )
            shutil.copy(src, self.ECHO_GZ)

        model = os.environ.get("LLM_MODEL", _DEFAULT_LLM_MODEL)
        for prefix, env_var in _MODEL_KEY_ENV.items():
            if model.startswith(prefix):
                if not os.environ.get(env_var):
                    self.fail(
                        f"LLM model '{model}' requires {env_var} to be set. "
                        f"Either set it in .env or choose a different model via LLM_MODEL."
                    )
                break

    def test_echo_manpage(self):
        model = os.environ.get("LLM_MODEL", _DEFAULT_LLM_MODEL)
        mp = extract(self.ECHO_GZ, model)
        flags = set()
        for opt in mp.options:
            flags.update(opt.short)
        self.assertIn("-n", flags, f"Expected -n in options, got: {flags}")
        self.assertIn("-e", flags, f"Expected -e in options, got: {flags}")


if __name__ == "__main__":
    unittest.main()
