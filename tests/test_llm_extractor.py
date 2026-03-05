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
    _dedup_ref_options,
    _extract_text_from_lines,
    _llm_option_to_store_option,
    _number_lines,
    _parse_json_response,
    _sanitize_option,
    _validate_llm_response,
    chunk_text,
    extract,
    get_manpage_text,
    get_plain_text,
)


# ---------------------------------------------------------------------------
# TestGetManpageText
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
# TestGetManpageTextReal — exercises the actual mandoc binary
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
# TestLlmOptionToStoreOption
# ---------------------------------------------------------------------------


class TestLlmOptionToStoreOption(unittest.TestCase):
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
            "has_argument": False,
            "lines": [10, 13],
        }
        opt = _llm_option_to_store_option(raw, self.orig)
        self.assertIsInstance(opt, store.Option)
        self.assertEqual(opt.short, ["-A"])
        self.assertEqual(opt.long, ["--catenate"])
        self.assertFalse(opt.has_argument)
        self.assertIn("**-A**, **--catenate**", opt.text)
        self.assertIn("Append files to an archive.", opt.text)

    def test_missing_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "has_argument": False}
        with self.assertRaises(ValueError):
            _llm_option_to_store_option(raw, self.orig)

    def test_invalid_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "has_argument": False, "lines": [10]}
        with self.assertRaises(ValueError):
            _llm_option_to_store_option(raw, self.orig)

    def test_bad_short_type_raises(self):
        raw = {"short": "-v", "long": [], "has_argument": False, "lines": [10, 13]}
        with self.assertRaises(ValueError):
            _llm_option_to_store_option(raw, self.orig)

    def test_nested_cmd_auto_corrects(self):
        raw = {
            "short": [],
            "long": ["--exec"],
            "has_argument": False,
            "nested_cmd": True,
            "lines": [10, 13],
        }
        opt = _llm_option_to_store_option(raw, self.orig)
        self.assertTrue(opt.nested_cmd)
        self.assertTrue(opt.has_argument)

    def test_positional_arg(self):
        raw = {
            "short": [],
            "long": [],
            "has_argument": False,
            "positional": "FILE",
            "lines": [10, 13],
        }
        opt = _llm_option_to_store_option(raw, self.orig)
        self.assertEqual(opt.positional, "FILE")


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

    def test_nested_cmd_forces_has_argument(self):
        short, long, ea, arg, nc = _sanitize_option(
            ["-exec"], [], False, None, True
        )
        self.assertTrue(ea)

    def test_via_llm_option_to_store(self):
        """argument is cleared when passed through full conversion."""
        orig = {10: "-D debugopts desc", 11: "some desc"}
        raw = {
            "short": ["-D"],
            "long": [],
            "has_argument": True,
            "positional": "debugopts",
            "nested_cmd": False,
            "lines": [10, 11],
        }
        opt = _llm_option_to_store_option(raw, orig)
        self.assertIsNone(opt.positional)
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
    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor.manpage.get_synopsis_and_aliases")
    def test_extract_returns_manpage(self, mock_synopsis, mock_text, mock_llm):
        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_text.return_value = "**-n**\n\nDo not output trailing newline.\n\n**-e**\n\nEnable escapes."
        # Lines: 1=**-n**, 2="", 3=Do not..., 4="", 5=**-e**, 6="", 7=Enable...
        mock_llm.return_value = (
            {
                "dashless_opts": False,
                "options": [
                    {"short": ["-n"], "long": [], "has_argument": False, "lines": [1, 3]},
                    {"short": ["-e"], "long": [], "has_argument": False, "lines": [5, 7]},
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

    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor.manpage.get_synopsis_and_aliases")
    def test_malformed_options_skipped(self, mock_synopsis, mock_text, mock_llm):
        mock_synopsis.return_value = (None, [("dummy", 10)])
        mock_text.return_value = "**-v**\n\nVerbose."
        mock_llm.return_value = (
            {
                "options": [
                    {"short": "not-a-list", "long": [], "lines": [1, 3]},
                    {"short": ["-v"], "long": [], "has_argument": False, "lines": [1, 3]},
                ],
            },
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
            '{"options": []}',
        )
        mp = extract("dummy.1.gz", "test-model")
        self.assertEqual(len(mp.options), 1)
        self.assertEqual(mp.options[0].short, ["-v"])

    @patch("explainshell.llm_extractor._call_llm")
    @patch("explainshell.llm_extractor.get_manpage_text")
    @patch("explainshell.llm_extractor.manpage.get_synopsis_and_aliases")
    def test_debug_dir_writes_files(self, mock_synopsis, mock_text, mock_llm):
        import tempfile

        mock_synopsis.return_value = ("a test tool", [("dummy", 10)])
        mock_text.return_value = "**-v**\n\nVerbose."
        raw_response = '{"options": [{"short": ["-v"], "long": [], "has_argument": false, "lines": [1, 3]}]}'
        mock_llm.return_value = (
            {
                "options": [
                    {"short": ["-v"], "long": [], "has_argument": False, "lines": [1, 3]},
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
            batch=None,
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
