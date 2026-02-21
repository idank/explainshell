"""Unit and integration tests for explainshell.llm_extractor."""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from explainshell import store
from explainshell.llm_extractor import (
    ExtractionError,
    CHUNK_SIZE_CHARS,
    CHUNK_OVERLAP_CHARS,
    _dedup_options,
    _llm_option_to_store_option,
    _parse_json_response,
    _validate_llm_response,
    chunk_text,
    extract,
    get_plain_text,
)


# ---------------------------------------------------------------------------
# TestGetPlainText
# ---------------------------------------------------------------------------

class TestGetPlainText(unittest.TestCase):
    @patch("explainshell.llm_extractor.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="some text", stderr="")
        result = get_plain_text("dummy.1.gz")
        self.assertEqual(result, "some text")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["mandoc", "-T", "txt"])

    @patch("explainshell.llm_extractor.subprocess.run")
    def test_empty_output_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="   ", stderr="")
        with self.assertRaises(ExtractionError):
            get_plain_text("dummy.1.gz")

    @patch("explainshell.llm_extractor.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with self.assertRaises(ExtractionError):
            get_plain_text("dummy.1.gz")


# ---------------------------------------------------------------------------
# TestChunkText
# ---------------------------------------------------------------------------

class TestChunkText(unittest.TestCase):
    def test_small_text_no_split(self):
        text = "hello world\n\nfoo bar"
        chunks = chunk_text(text)
        self.assertEqual(chunks, [text])

    def test_large_text_splits(self):
        # build text large enough to exceed CHUNK_SIZE_CHARS
        para = "x" * 1000
        paragraphs = [para] * (CHUNK_SIZE_CHARS // 1000 + 5)
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), CHUNK_SIZE_CHARS + 1000)  # at most one para over

    def test_overlap_exists(self):
        para = "y" * 1000
        paragraphs = [para] * (CHUNK_SIZE_CHARS // 1000 + 5)
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text)
        if len(chunks) >= 2:
            # the end of chunk[0] and start of chunk[1] should share content
            end_of_first = chunks[0][-CHUNK_OVERLAP_CHARS:]
            start_of_second = chunks[1][:CHUNK_OVERLAP_CHARS]
            # there should be some common paragraphs
            self.assertTrue(
                end_of_first in chunks[1] or start_of_second in chunks[0],
                "No overlap detected between consecutive chunks",
            )

    def test_single_paragraph_larger_than_chunk(self):
        text = "z" * (CHUNK_SIZE_CHARS + 1)
        chunks = chunk_text(text)
        # single oversized paragraph → still one chunk
        self.assertEqual(len(chunks), 1)


# ---------------------------------------------------------------------------
# TestParseJsonResponse
# ---------------------------------------------------------------------------

class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        content = '{"options": []}'
        result = _parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_strips_markdown_fences(self):
        content = "```json\n{\"options\": []}\n```"
        result = _parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_strips_backtick_fence_no_lang(self):
        content = "```\n{\"options\": []}\n```"
        result = _parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_no_json_raises(self):
        with self.assertRaises(ExtractionError):
            _parse_json_response("There is no JSON here at all.")

    def test_extracts_outermost_braces(self):
        content = 'prefix text {"options": []} suffix text'
        result = _parse_json_response(content)
        self.assertEqual(result, {"options": []})


# ---------------------------------------------------------------------------
# TestValidateLlmResponse
# ---------------------------------------------------------------------------

class TestValidateLlmResponse(unittest.TestCase):
    def test_valid_passes(self):
        _validate_llm_response({"options": []})  # no exception

    def test_missing_options_raises(self):
        with self.assertRaises(ValueError):
            _validate_llm_response({"foo": "bar"})

    def test_options_not_list_raises(self):
        with self.assertRaises(ValueError):
            _validate_llm_response({"options": "not a list"})

    def test_option_not_dict_raises(self):
        with self.assertRaises(ValueError):
            _validate_llm_response({"options": ["string"]})


# ---------------------------------------------------------------------------
# TestLlmOptionToStoreOption
# ---------------------------------------------------------------------------

class TestLlmOptionToStoreOption(unittest.TestCase):
    def test_short_and_long(self):
        raw = {
            "short": ["-v"],
            "long": ["--verbose"],
            "expects_arg": False,
            "argument": None,
            "nested_cmd": False,
            "description": "Be verbose.",
        }
        opt = _llm_option_to_store_option(raw, 0)
        self.assertIsInstance(opt, store.Option)
        self.assertEqual(opt.short, ["-v"])
        self.assertEqual(opt.long, ["--verbose"])
        self.assertFalse(opt.expects_arg)
        self.assertIsNone(opt.argument)
        self.assertFalse(opt.nested_cmd)

    def test_expects_arg_list(self):
        raw = {
            "short": [],
            "long": ["--color"],
            "expects_arg": ["always", "never", "auto"],
            "argument": None,
            "nested_cmd": False,
            "description": "Colorize output.",
        }
        opt = _llm_option_to_store_option(raw, 1)
        self.assertEqual(opt.expects_arg, ["always", "never", "auto"])

    def test_nested_cmd_auto_corrects_expects_arg(self):
        raw = {
            "short": [],
            "long": ["--exec"],
            "expects_arg": False,
            "argument": None,
            "nested_cmd": True,
            "description": "Execute command.",
        }
        opt = _llm_option_to_store_option(raw, 2)
        self.assertTrue(opt.nested_cmd)
        self.assertTrue(opt.expects_arg)  # auto-corrected

    def test_positional_arg(self):
        raw = {
            "short": [],
            "long": [],
            "expects_arg": False,
            "argument": "FILE",
            "nested_cmd": False,
            "description": "Input file.",
        }
        opt = _llm_option_to_store_option(raw, 3)
        self.assertEqual(opt.argument, "FILE")
        self.assertEqual(opt.short, [])
        self.assertEqual(opt.long, [])


# ---------------------------------------------------------------------------
# TestDedupOptions
# ---------------------------------------------------------------------------

class TestDedupOptions(unittest.TestCase):
    def test_duplicates_removed(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "description": "first"},
            {"short": ["-v"], "long": ["--verbose"], "description": "duplicate"},
        ]
        result = _dedup_options(opts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["description"], "first")

    def test_different_options_kept(self):
        opts = [
            {"short": ["-v"], "long": [], "description": "verbose"},
            {"short": ["-q"], "long": [], "description": "quiet"},
        ]
        result = _dedup_options(opts)
        self.assertEqual(len(result), 2)

    def test_positional_always_kept(self):
        opts = [
            {"short": [], "long": [], "description": "FILE 1"},
            {"short": [], "long": [], "description": "FILE 2"},
        ]
        result = _dedup_options(opts)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# TestExtractIntegration
# ---------------------------------------------------------------------------

class TestExtractIntegration(unittest.TestCase):
    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_plain_text")
    @patch("explainshell.llm_extractor._get_synopsis_and_aliases")
    def test_extract_returns_manpage(self, mock_synopsis, mock_plaintext, mock_llm):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_plaintext.return_value = "dummy man page text"
        mock_llm.return_value = [
            {
                "short": ["-n"],
                "long": [],
                "expects_arg": False,
                "argument": None,
                "nested_cmd": False,
                "description": "Do not output trailing newline.",
            },
            {
                "short": ["-e"],
                "long": [],
                "expects_arg": False,
                "argument": None,
                "nested_cmd": False,
                "description": "Enable interpretation of backslash escapes.",
            },
        ]
        mp = extract("dummy.1.gz", "test-model")
        self.assertIsInstance(mp, store.ManPage)
        self.assertEqual(len(mp.options), 2)
        flags = [opt.short[0] for opt in mp.options]
        self.assertIn("-n", flags)
        self.assertIn("-e", flags)

    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_plain_text")
    @patch("explainshell.llm_extractor._get_synopsis_and_aliases")
    def test_malformed_options_skipped(self, mock_synopsis, mock_plaintext, mock_llm):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_plaintext.return_value = "some text"
        mock_llm.return_value = [
            {"short": "not-a-list", "long": [], "expects_arg": False, "description": "bad"},
            {
                "short": ["-v"],
                "long": [],
                "expects_arg": False,
                "argument": None,
                "nested_cmd": False,
                "description": "Verbose.",
            },
        ]
        mp = extract("dummy.1.gz", "test-model")
        # only the valid option should be kept
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-v"])


# ---------------------------------------------------------------------------
# Real-LLM integration test (skipped unless RUN_LLM_TESTS=1)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("RUN_LLM_TESTS") == "1", "set RUN_LLM_TESTS=1 to run")
class TestRealLlm(unittest.TestCase):
    ECHO_GZ = os.path.join(os.path.dirname(__file__), "echo.1.gz")

    def setUp(self):
        if not os.path.exists(self.ECHO_GZ):
            # copy from manpages dir if not present
            import shutil
            src = os.path.join(
                os.path.dirname(__file__), "..", "manpages", "1", "echo.1.gz"
            )
            shutil.copy(src, self.ECHO_GZ)

    def test_echo_manpage(self):
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
        mp = extract(self.ECHO_GZ, model)
        flags = set()
        for opt in mp.options:
            flags.update(opt.short)
        self.assertIn("-n", flags, f"Expected -n in options, got: {flags}")
        self.assertIn("-e", flags, f"Expected -e in options, got: {flags}")


if __name__ == "__main__":
    unittest.main()
