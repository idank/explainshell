"""Tests for explainshell.extraction.llm.LLMExtractor — integration and LLM extraction."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import TESTS_DIR

from explainshell import models
from explainshell.errors import ExtractionError
from explainshell.extraction import ExtractorConfig
from explainshell.extraction.llm.extractor import (
    ChunkResult,
    LLMExtractor,
    PreparedFile,
)
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
                    {"short": ["-x"], "long": [], "lines": "bad"},
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
# TestMultiChunk — interactive extract() and batch finalize()
# ---------------------------------------------------------------------------

# Shared helpers for multi-chunk tests.
_PLAIN_TEXT = "**-a**\n\nOption a.\n\n**-b**\n\nOption b."
_ORIGINAL_LINES: dict[int, str] = {
    1: "**-a**",
    2: "",
    3: "Option a.",
    4: "",
    5: "**-b**",
    6: "",
    7: "Option b.",
}


def _make_prepared(n_chunks: int = 2) -> PreparedFile:
    return PreparedFile(
        synopsis="test tool",
        aliases=[("dummy", 10)],
        original_lines=_ORIGINAL_LINES,
        basename="dummy",
        numbered_text="   1| **-a**\n   2| ...",
        plain_text_len=len(_PLAIN_TEXT),
        plain_text=_PLAIN_TEXT,
        requests=["chunk0 content", "chunk1 content"][:n_chunks],
    )


def _chunk_result(options: list[dict]) -> ChunkResult:
    return ChunkResult(
        data={"options": options},
        messages=[
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
        ],
        raw_response='{"options": []}',
        usage=TokenUsage(0, 0),
    )


_CHUNK0_OPTIONS: list[dict] = [
    {"short": ["-a"], "long": [], "has_argument": False, "lines": [1, 3]},
]
_CHUNK1_OPTIONS: list[dict] = [
    {"short": ["-b"], "long": [], "has_argument": False, "lines": [5, 7]},
]

_CHUNK0_JSON = (
    '{"options": [{"short": ["-a"], "long": [], '
    '"has_argument": false, "lines": [1, 3]}]}'
)
_CHUNK1_JSON = (
    '{"options": [{"short": ["-b"], "long": [], '
    '"has_argument": false, "lines": [5, 7]}]}'
)


class TestMultiChunkExtract(unittest.TestCase):
    """Interactive extract() path with multiple chunks."""

    def _make_extractor(self) -> LLMExtractor:
        cfg = ExtractorConfig(model="test-model")
        return LLMExtractor(cfg)

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    @patch("explainshell.extraction.llm.extractor.LLMExtractor._call_llm")
    @patch("explainshell.extraction.llm.extractor.LLMExtractor.prepare")
    def test_multi_chunk_merges_options(
        self,
        mock_prepare,
        mock_llm,
        mock_sha,
        mock_common_synopsis,
        mock_nested_cmd,
    ):
        mock_prepare.return_value = _make_prepared(2)
        mock_common_synopsis.return_value = ("test tool", [("dummy", 10)])
        mock_llm.side_effect = [
            _chunk_result(_CHUNK0_OPTIONS),
            _chunk_result(_CHUNK1_OPTIONS),
        ]

        ext = self._make_extractor()
        result = ext.extract("dummy.1.gz")
        self.assertEqual(len(result.mp.options), 2)
        flags = {opt.short[0] for opt in result.mp.options}
        self.assertEqual(flags, {"-a", "-b"})
        self.assertEqual(mock_llm.call_count, 2)

    def test_call_llm_invalid_response_preserves_raw(self):
        """_call_llm propagates raw_response when the LLM returns invalid JSON structure."""
        bad_response = '{"error": "waiting for remaining parts"}'
        mock_provider = MagicMock()
        mock_provider.call.return_value = (bad_response, TokenUsage(10, 5))
        mock_provider.retryable_exceptions = (ConnectionError,)

        ext = self._make_extractor()
        ext._provider_instance = mock_provider

        with self.assertRaises(ExtractionError) as ctx:
            ext._call_llm("some user content")
        self.assertIn("missing 'options' key", str(ctx.exception))
        self.assertEqual(ctx.exception.raw_response, bad_response)

    @patch("explainshell.extraction.llm.extractor.LLMExtractor.prepare")
    def test_extract_invalid_chunk0_dumps_failed_response(self, mock_prepare):
        """Interactive extract() dumps the raw response when chunk 0 fails validation."""
        bad_response = '{"error": "waiting for remaining parts"}'
        mock_provider = MagicMock()
        mock_provider.call.return_value = (bad_response, TokenUsage(10, 5))
        mock_provider.retryable_exceptions = (ConnectionError,)

        mock_prepare.return_value = _make_prepared(1)

        with tempfile.TemporaryDirectory() as fail_dir:
            cfg = ExtractorConfig(model="test-model", fail_dir=fail_dir)
            ext = LLMExtractor(cfg)
            ext._provider_instance = mock_provider

            with self.assertRaises(ExtractionError):
                ext.extract("dummy.1.gz")

            failed_files = os.listdir(fail_dir)
            self.assertEqual(len(failed_files), 1)
            with open(os.path.join(fail_dir, failed_files[0])) as f:
                self.assertEqual(f.read(), bad_response)


class TestMultiChunkFinalize(unittest.TestCase):
    """Batch finalize() path with multiple chunks."""

    def _make_extractor(self, fail_dir: str | None = None) -> LLMExtractor:
        cfg = ExtractorConfig(model="test-model", fail_dir=fail_dir)
        return LLMExtractor(cfg)

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_multi_chunk_merges_options(
        self, mock_sha, mock_common_synopsis, mock_nested_cmd
    ):
        mock_common_synopsis.return_value = ("test tool", [("dummy", 10)])
        prepared = _make_prepared(2)
        ext = self._make_extractor()
        result = ext.finalize("dummy.1.gz", prepared, [_CHUNK0_JSON, _CHUNK1_JSON])
        self.assertEqual(len(result.mp.options), 2)
        flags = {opt.short[0] for opt in result.mp.options}
        self.assertEqual(flags, {"-a", "-b"})

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_chunk0_invalid_raises_extraction_error(
        self, mock_sha, mock_common_synopsis, mock_nested_cmd
    ):
        """A chunk-0 response missing 'options' raises ExtractionError, not ValueError."""
        mock_common_synopsis.return_value = ("test tool", [("dummy", 10)])
        prepared = _make_prepared(2)
        bad_chunk0 = '{"error": "waiting for remaining parts"}'
        ext = self._make_extractor()
        with self.assertRaises(ExtractionError) as ctx:
            ext.finalize("dummy.1.gz", prepared, [bad_chunk0, _CHUNK1_JSON])
        self.assertIn("missing 'options' key", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.raw_response)

    @patch(
        "explainshell.extraction.common.roff_utils.detect_nested_cmd",
        return_value=False,
    )
    @patch("explainshell.extraction.common.manpage.get_synopsis_and_aliases")
    @patch("explainshell.extraction.common.gz_sha256", return_value="abc123")
    def test_finalize_normalizes_subcommands(
        self, mock_sha, mock_common_synopsis, mock_nested_cmd
    ):
        """finalize() strips parent prefix from subcommand names."""
        mock_common_synopsis.return_value = ("test tool", [("dummy", 10)])
        prepared = _make_prepared(1)
        prepared = PreparedFile(
            synopsis=prepared.synopsis,
            aliases=prepared.aliases,
            original_lines=prepared.original_lines,
            basename="git",
            numbered_text=prepared.numbered_text,
            plain_text_len=prepared.plain_text_len,
            plain_text=prepared.plain_text,
            requests=prepared.requests[:1],
        )
        chunk_json = (
            '{"options": [{"short": ["-v"], "long": [], '
            '"has_argument": false, "lines": [1, 3]}], '
            '"subcommands": ["git-add", "git-commit", "push"]}'
        )
        ext = self._make_extractor()
        result = ext.finalize("git.1.gz", prepared, [chunk_json])
        self.assertEqual(result.mp.subcommands, ["add", "commit", "push"])


# ---------------------------------------------------------------------------
# TestSubcommandNormalization
# ---------------------------------------------------------------------------


class TestSubcommandNormalization(unittest.TestCase):
    """Tests for normalize_subcommands()."""

    def test_strips_parent_prefix(self):
        from explainshell.extraction.llm.response import normalize_subcommands

        result = normalize_subcommands("git", ["git-add", "git-commit", "push"])
        self.assertEqual(result, ["add", "commit", "push"])

    def test_no_prefix_unchanged(self):
        from explainshell.extraction.llm.response import normalize_subcommands

        result = normalize_subcommands("apt", ["install", "update", "remove"])
        self.assertEqual(result, ["install", "update", "remove"])

    def test_deduplicates(self):
        from explainshell.extraction.llm.response import normalize_subcommands

        result = normalize_subcommands("git", ["git-add", "add", "git-add"])
        self.assertEqual(result, ["add"])


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
        self.assertIn("Extract ALL options documented in THIS part only", content)
        self.assertIn("do not wait for other parts", content)
        self.assertIn('{"options": []}', content)
        self.assertIn("chunk", content)


# ---------------------------------------------------------------------------
# Real-LLM integration test (skipped unless RUN_LLM_TESTS=1)
# ---------------------------------------------------------------------------

# Models to test against.  Each entry is (model_string, env_var_required).
# codex/ models authenticate via the CLI itself (no env var needed).
_REAL_LLM_MODELS = [
    ("openai/gpt-5-mini", "OPENAI_API_KEY"),
    ("codex/gpt-5.4-mini", None),
]

ECHO_GZ = os.path.join(TESTS_DIR, "echo.1.gz")


def _ensure_echo_gz() -> None:
    if not os.path.exists(ECHO_GZ):
        import shutil

        src = os.path.join(TESTS_DIR, "manpages", "ubuntu", "12.04", "1", "echo.1.gz")
        shutil.copy(src, ECHO_GZ)


@pytest.mark.parametrize(
    "model,env_var",
    _REAL_LLM_MODELS,
    ids=[m for m, _ in _REAL_LLM_MODELS],
)
def test_real_llm_echo_manpage(model: str, env_var: str | None) -> None:
    if os.environ.get("RUN_LLM_TESTS") != "1":
        pytest.skip("set RUN_LLM_TESTS=1 to run")
    if env_var and not os.environ.get(env_var):
        pytest.skip(f"{env_var} not set")

    _ensure_echo_gz()

    cfg = ExtractorConfig(model=model)
    ext = LLMExtractor(cfg)
    result = ext.extract(ECHO_GZ)
    flags: set[str] = set()
    for opt in result.mp.options:
        flags.update(opt.short)
    assert "-n" in flags, f"Expected -n in options, got: {flags}"
    assert "-e" in flags, f"Expected -e in options, got: {flags}"


if __name__ == "__main__":
    unittest.main()
