"""Tests for explainshell.extraction.llm.LLMExtractor — integration and LLM extraction."""

import os
import unittest
from unittest.mock import patch

from tests.helpers import TESTS_DIR

from explainshell import models
from explainshell.extraction import ExtractorConfig
from explainshell.extraction.llm.extractor import ChunkResult, LLMExtractor
from explainshell.extraction.llm.providers import TokenUsage


# ---------------------------------------------------------------------------
# TestExtractIntegration
# ---------------------------------------------------------------------------


class TestExtractIntegration(unittest.TestCase):
    def _make_extractor(self, model="test-model", debug_dir=None, fail_dir=None):
        cfg = ExtractorConfig(model=model, debug_dir=debug_dir, fail_dir=fail_dir)
        return LLMExtractor(cfg)

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.llm.extractor.LLMExtractor._call_llm")
    @patch("explainshell.extraction.llm.extractor.get_manpage_text")
    @patch("explainshell.extraction.llm.extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_extract_returns_manpage(
        self,
        mock_sha,
        mock_synopsis,
        mock_text,
        mock_llm,
        mock_common_synopsis,
        mock_nested_cmd,
    ):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_common_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_text.return_value = (
            "**-n**\n\nDo not output trailing newline.\n\n**-e**\n\nEnable escapes."
        )
        mock_llm.return_value = ChunkResult(
            data={
                "dashless_opts": False,
                "options": [
                    {
                        "short": ["-n"],
                        "long": [],
                        "has_argument": False,
                        "lines": [1, 3],
                    },
                    {
                        "short": ["-e"],
                        "long": [],
                        "has_argument": False,
                        "lines": [5, 7],
                    },
                ],
            },
            messages=[
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
            ],
            raw_response='{"options": []}',
            usage=TokenUsage(0, 0),
        )
        ext = self._make_extractor()
        result = ext.extract("dummy.1.gz")
        mp, raw = result.mp, result.raw
        self.assertIsInstance(mp, models.ParsedManpage)
        self.assertIsInstance(raw, models.RawManpage)
        self.assertEqual(len(mp.options), 2)
        flags = [opt.short[0] for opt in mp.options]
        self.assertIn("-n", flags)
        self.assertIn("-e", flags)
        n_opt = next(o for o in mp.options if "-n" in o.short)
        self.assertIn("Do not output trailing newline.", n_opt.text)
        self.assertEqual(raw.generator, "mandoc -T markdown")
        self.assertIn("-n", raw.source_text)

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.llm.extractor.LLMExtractor._call_llm")
    @patch("explainshell.extraction.llm.extractor.get_manpage_text")
    @patch("explainshell.extraction.llm.extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_malformed_options_skipped(
        self,
        mock_sha,
        mock_synopsis,
        mock_text,
        mock_llm,
        mock_common_synopsis,
        mock_nested_cmd,
    ):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_common_synopsis.return_value = (None, [("dummy", 10)])
        mock_text.return_value = "**-v**\n\nVerbose."
        mock_llm.return_value = ChunkResult(
            data={
                "options": [
                    {"short": "not-a-list", "long": [], "lines": [1, 3]},
                    {
                        "short": ["-v"],
                        "long": [],
                        "has_argument": False,
                        "lines": [1, 3],
                    },
                ],
            },
            messages=[
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
            ],
            raw_response='{"options": []}',
            usage=TokenUsage(0, 0),
        )
        ext = self._make_extractor()
        result = ext.extract("dummy.1.gz")
        self.assertEqual(len(result.mp.options), 1)
        self.assertEqual(result.mp.options[0].short, ["-v"])

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.llm.extractor.LLMExtractor._call_llm")
    @patch("explainshell.extraction.llm.extractor.get_manpage_text")
    @patch("explainshell.extraction.llm.extractor.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_debug_dir_writes_files(
        self,
        mock_sha,
        mock_synopsis,
        mock_text,
        mock_llm,
        mock_common_synopsis,
        mock_nested_cmd,
    ):
        import tempfile

        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_common_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_text.return_value = "**-v**\n\nVerbose."
        raw_response = '{"options": [{"short": ["-v"], "long": [], "has_argument": false, "lines": [1, 3]}]}'
        mock_llm.return_value = ChunkResult(
            data={
                "options": [
                    {
                        "short": ["-v"],
                        "long": [],
                        "has_argument": False,
                        "lines": [1, 3],
                    },
                ]
            },
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
            ],
            raw_response=raw_response,
            usage=TokenUsage(0, 0),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ext = self._make_extractor(debug_dir=tmpdir)
            result = ext.extract("dummy.1.gz")
            self.assertEqual(len(result.mp.options), 1)
            md_path = os.path.join(tmpdir, "dummy.md")
            self.assertTrue(os.path.exists(md_path))
            prompt_path = os.path.join(tmpdir, "dummy.prompt.json")
            self.assertTrue(os.path.exists(prompt_path))
            with open(prompt_path) as f:
                import json

                msgs = json.load(f)
                self.assertEqual(len(msgs), 2)
                self.assertEqual(msgs[0]["role"], "system")
            response_path = os.path.join(tmpdir, "dummy.response.txt")
            self.assertTrue(os.path.exists(response_path))
            with open(response_path) as f:
                self.assertEqual(f.read(), raw_response)


# ---------------------------------------------------------------------------
# TestBuildUserContent
# ---------------------------------------------------------------------------


class TestBuildUserContent(unittest.TestCase):
    def test_includes_chunk_text(self):
        content = LLMExtractor._build_user_content("chunk text", "")
        self.assertIn("chunk text", content)

    def test_chunk_info_included(self):
        content = LLMExtractor._build_user_content("chunk", " (part 1 of 3)")
        self.assertIn("(part 1 of 3)", content)


# ---------------------------------------------------------------------------
# Real-LLM integration test (skipped unless RUN_LLM_TESTS=1)
# ---------------------------------------------------------------------------

# Map model prefixes to the environment variable they need.
_MODEL_KEY_ENV = {
    "gemini/": "GEMINI_API_KEY",
    "openai/": "OPENAI_API_KEY",
}

_DEFAULT_LLM_MODEL = "gemini/gemini-3-flash-preview"


@unittest.skipUnless(
    os.environ.get("RUN_LLM_TESTS") == "1", "set RUN_LLM_TESTS=1 to run"
)
class TestRealLlm(unittest.TestCase):
    ECHO_GZ = os.path.join(TESTS_DIR, "echo.1.gz")

    def setUp(self):
        if not os.path.exists(self.ECHO_GZ):
            # copy from manpages dir if not present
            import shutil

            src = os.path.join(
                TESTS_DIR,
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
        cfg = ExtractorConfig(model=model)
        ext = LLMExtractor(cfg)
        result = ext.extract(self.ECHO_GZ)
        flags = set()
        for opt in result.mp.options:
            flags.update(opt.short)
        self.assertIn("-n", flags, f"Expected -n in options, got: {flags}")
        self.assertIn("-e", flags, f"Expected -e in options, got: {flags}")


if __name__ == "__main__":
    unittest.main()
