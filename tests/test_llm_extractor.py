"""Unit and integration tests for explainshell.llm_extractor."""

import argparse
import os
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
    _sanitize_option,
    _validate_llm_response,
    chunk_text,
    extract,
    get_manpage_text,
    get_plain_text,
)


# ---------------------------------------------------------------------------
# TestGetPlainText
# ---------------------------------------------------------------------------


class TestGetManpageText(unittest.TestCase):
    @patch("explainshell.llm_extractor.subprocess.run")
    def test_success_returns_markdown(self, mock_run):
        md_output = "# NAME\n\ngrep - search for patterns\n\n**-v**, **--invert-match**\n"
        mock_run.return_value = MagicMock(returncode=0, stdout=md_output, stderr="")
        result = get_manpage_text("dummy.1.gz")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("-T", cmd)
        self.assertIn("markdown", cmd)
        self.assertEqual(result, md_output.strip())

    @patch("explainshell.llm_extractor.subprocess.run")
    def test_empty_output_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="   ", stderr="")
        with self.assertRaises(ExtractionError):
            get_manpage_text("dummy.1.gz")

    @patch("explainshell.llm_extractor.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with self.assertRaises(ExtractionError):
            get_manpage_text("dummy.1.gz")

    def test_backward_compat_alias(self):
        """get_plain_text is an alias for get_manpage_text."""
        self.assertIs(get_plain_text, get_manpage_text)


# ---------------------------------------------------------------------------
# TestGetPlainTextReal — exercises the actual mandoc binary
# ---------------------------------------------------------------------------

_ECHO_GZ = os.path.join(
    os.path.dirname(__file__), "manpages", "ubuntu", "12.04", "1", "echo.1.gz"
)
_FIND_GZ = os.path.join(
    os.path.dirname(__file__), "manpages", "ubuntu", "12.04", "1", "find.1.gz"
)


class TestGetManpageTextReal(unittest.TestCase):
    def test_mandoc_produces_markdown(self):
        text = get_manpage_text(_ECHO_GZ)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_mandoc_output_contains_option(self):
        text = get_manpage_text(_ECHO_GZ)
        self.assertIn("-n", text)

    def test_mandoc_output_has_markdown_formatting(self):
        text = get_manpage_text(_ECHO_GZ)
        # Should contain some markdown bold or italic markers
        self.assertTrue(
            "**" in text or "*" in text,
            f"Expected markdown formatting in output, got: {text[:300]}",
        )

    def test_no_non_breaking_spaces(self):
        """mandoc emits &#x00A0; between flags and args; these should be regular spaces."""
        text = get_manpage_text(_FIND_GZ)
        self.assertNotIn("\xa0", text)

    def test_no_artificial_line_wrapping(self):
        """Prose paragraphs should not be hard-wrapped at terminal width (~78 cols)."""
        text = get_manpage_text(_FIND_GZ)
        content_lines = [
            line
            for line in text.split("\n")
            if line and not line.startswith("=") and not line.startswith("[")
        ]
        long_lines = [line for line in content_lines if len(line) > 80]
        self.assertGreater(
            len(long_lines),
            0,
            "Expected long prose lines (>80 chars) but all lines were short — "
            "mandoc whitespace may be leaking through as hard wraps",
        )


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
            self.assertLessEqual(
                len(chunk), CHUNK_SIZE_CHARS + 1000
            )  # at most one para over

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
        content = '```json\n{"options": []}\n```'
        result = _parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_strips_backtick_fence_no_lang(self):
        content = '```\n{"options": []}\n```'
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
        opt = _llm_option_to_store_option(raw)
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
        opt = _llm_option_to_store_option(raw)
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
        opt = _llm_option_to_store_option(raw)
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
        opt = _llm_option_to_store_option(raw)
        self.assertEqual(opt.argument, "FILE")
        self.assertEqual(opt.short, [])
        self.assertEqual(opt.long, [])


# ---------------------------------------------------------------------------
# TestSanitizeOption
# ---------------------------------------------------------------------------


class TestSanitizeOption(unittest.TestCase):
    def test_argument_cleared_when_short_present(self):
        short, long, ea, arg, nc = _sanitize_option(
            ["-D"], [], True, "debugopts", False
        )
        self.assertIsNone(arg)

    def test_argument_cleared_when_long_present(self):
        short, long, ea, arg, nc = _sanitize_option(
            [], ["--type"], True, "c", False
        )
        self.assertIsNone(arg)

    def test_argument_kept_for_positional(self):
        short, long, ea, arg, nc = _sanitize_option(
            [], [], False, "FILE", False
        )
        self.assertEqual(arg, "FILE")

    def test_nested_cmd_forces_expects_arg(self):
        short, long, ea, arg, nc = _sanitize_option(
            ["-exec"], [], False, None, True
        )
        self.assertTrue(ea)

    def test_via_llm_option_to_store(self):
        """argument is cleared when passed through full conversion."""
        raw = {
            "short": ["-D"],
            "long": [],
            "expects_arg": True,
            "argument": "debugopts",
            "nested_cmd": False,
            "description": "-D debugopts desc",
        }
        opt = _llm_option_to_store_option(raw)
        self.assertIsNone(opt.argument)
        self.assertEqual(opt.short, ["-D"])


# ---------------------------------------------------------------------------
# TestDedupOptions
# ---------------------------------------------------------------------------


class TestDedupOptions(unittest.TestCase):
    def test_duplicates_keep_longest_description(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "description": "short"},
            {"short": ["-v"], "long": ["--verbose"], "description": "a much longer description wins"},
        ]
        result = _dedup_options(opts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["description"], "a much longer description wins")

    def test_duplicates_same_length_keeps_one(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "description": "first"},
            {"short": ["-v"], "long": ["--verbose"], "description": "second"},
        ]
        result = _dedup_options(opts)
        self.assertEqual(len(result), 1)

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
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor._get_synopsis_and_aliases")
    def test_extract_returns_manpage(self, mock_synopsis, mock_plaintext, mock_llm):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_plaintext.return_value = "dummy man page text"
        mock_llm.return_value = (
            {
                "dashless_opts": False,
                "options": [
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

    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor._get_synopsis_and_aliases")
    def test_malformed_options_skipped(self, mock_synopsis, mock_plaintext, mock_llm):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_plaintext.return_value = "some text"
        mock_llm.return_value = (
            {
                "options": [
                    {
                        "short": "not-a-list",
                        "long": [],
                        "expects_arg": False,
                        "description": "bad",
                    },
                    {
                        "short": ["-v"],
                        "long": [],
                        "expects_arg": False,
                        "argument": None,
                        "nested_cmd": False,
                        "description": "Verbose.",
                    },
                ],
            },
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
            '{"options": []}',
        )
        mp = extract("dummy.1.gz", "test-model")
        # only the valid option should be kept
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-v"])

    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor._get_synopsis_and_aliases")
    def test_debug_dir_writes_files(self, mock_synopsis, mock_plaintext, mock_llm):
        import tempfile

        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_plaintext.return_value = "dummy man page text"
        raw_response = '{"options": [{"short": ["-v"], "long": [], "expects_arg": false, "argument": null, "nested_cmd": false, "description": "Verbose."}]}'
        mock_llm.return_value = (
            {
                "options": [
                    {
                        "short": ["-v"],
                        "long": [],
                        "expects_arg": False,
                        "argument": None,
                        "nested_cmd": False,
                        "description": "Verbose.",
                    }
                ]
            },
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
            raw_response,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = extract("dummy.1.gz", "test-model", debug_dir=tmpdir)
            self.assertEqual(len(mp.options), 1)
            # Check markdown file
            md_path = os.path.join(tmpdir, "dummy.md")
            self.assertTrue(os.path.exists(md_path))
            with open(md_path) as f:
                self.assertEqual(f.read(), "dummy man page text")
            # Check prompt file
            prompt_path = os.path.join(tmpdir, "dummy.prompt.json")
            self.assertTrue(os.path.exists(prompt_path))
            with open(prompt_path) as f:
                import json

                msgs = json.load(f)
                self.assertEqual(len(msgs), 2)
                self.assertEqual(msgs[0]["role"], "system")
            # Check response file
            response_path = os.path.join(tmpdir, "dummy.response.txt")
            self.assertTrue(os.path.exists(response_path))
            with open(response_path) as f:
                self.assertEqual(f.read(), raw_response)


# ---------------------------------------------------------------------------
# TestLlmManagerDryRun
# ---------------------------------------------------------------------------


class TestLlmManagerDryRun(unittest.TestCase):
    """Tests for --dry-run: LLM is called, DB is not written."""

    def _make_args(self, dry_run=True, overwrite=False, mode="llm:test-model"):
        args = argparse.Namespace(
            mode=mode,
            db="/tmp/test.db",
            overwrite=overwrite,
            drop=False,
            dry_run=dry_run,
            diff=False,
            debug_dir="debug-output",
            log="WARNING",
            jobs=1,
            files=[],
        )
        return args

    @patch("explainshell.manager.llm_extractor.extract")
    @patch("explainshell.manager.store.Store")
    @patch("explainshell.manager._collect_gz_files")
    def test_dry_run_calls_llm_but_not_store(
        self, mock_collect, mock_store_cls, mock_extract
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_mp = MagicMock()
        fake_mp.options = [MagicMock(), MagicMock()]
        mock_extract.return_value = fake_mp

        from explainshell.manager import main

        args = self._make_args(dry_run=True)
        ret = main(args)

        mock_extract.assert_called_once_with(
            "/fake/echo.1.gz", "test-model", debug_dir="debug-output"
        )
        mock_store_cls.assert_not_called()
        self.assertEqual(ret, 0)

    @patch("explainshell.manager.llm_extractor.extract")
    @patch("explainshell.manager.store.Store")
    @patch("explainshell.manager._collect_gz_files")
    def test_normal_run_writes_to_store(
        self, mock_collect, mock_store_cls, mock_extract
    ):
        mock_collect.return_value = ["/fake/echo.1.gz"]
        fake_mp = MagicMock()
        fake_mp.options = [MagicMock()]
        fake_mp.source = "echo.1.gz"
        mock_extract.return_value = fake_mp

        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        # simulate page not already stored
        from explainshell import errors

        mock_store.find_man_page.side_effect = errors.ProgramDoesNotExist("echo")

        from explainshell.manager import main

        args = self._make_args(dry_run=False)
        main(args)

        mock_extract.assert_called_once()
        mock_store.add_manpage.assert_called_once_with(fake_mp)


# ---------------------------------------------------------------------------
# Real-LLM integration test (skipped unless RUN_LLM_TESTS=1)
# ---------------------------------------------------------------------------

# Map LiteLLM model prefixes to the environment variable they need.
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
class TestRealLlm(unittest.TestCase):
    ECHO_GZ = os.path.join(os.path.dirname(__file__), "echo.1.gz")

    def setUp(self):
        if not os.path.exists(self.ECHO_GZ):
            # copy from manpages dir if not present
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
