"""Tests for explainshell.extraction.llm.response — LLM response parsing and option conversion."""

import unittest

from explainshell import models
from explainshell.errors import ExtractionError
from explainshell.extraction.llm.response import (
    dedup_options,
    dedup_ref_options,
    extract_text_from_lines,
    llm_option_to_store_option,
    parse_json_response,
    process_llm_result,
    sanitize_option_fields,
    validate_llm_response,
)

# ---------------------------------------------------------------------------
# TestParseJsonResponse
# ---------------------------------------------------------------------------


class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        content = '{"options": []}'
        result = parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_strips_markdown_fences(self):
        content = '```json\n{"options": []}\n```'
        result = parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_strips_backtick_fence_no_lang(self):
        content = '```\n{"options": []}\n```'
        result = parse_json_response(content)
        self.assertEqual(result, {"options": []})

    def test_no_json_raises(self):
        with self.assertRaises(ExtractionError):
            parse_json_response("There is no JSON here at all.")

    def test_extracts_outermost_braces(self):
        content = 'prefix text {"options": []} suffix text'
        result = parse_json_response(content)
        self.assertEqual(result, {"options": []})


# ---------------------------------------------------------------------------
# TestValidateLlmResponse
# ---------------------------------------------------------------------------


class TestValidateLlmResponse(unittest.TestCase):
    def test_valid_passes(self):
        validate_llm_response({"options": []})  # no exception

    def test_missing_options_raises(self):
        with self.assertRaises(ValueError):
            validate_llm_response({"foo": "bar"})

    def test_options_not_list_raises(self):
        with self.assertRaises(ValueError):
            validate_llm_response({"options": "not a list"})

    def test_option_not_dict_raises(self):
        with self.assertRaises(ValueError):
            validate_llm_response({"options": ["string"]})


# ---------------------------------------------------------------------------
# TestProcessLlmResult
# ---------------------------------------------------------------------------


class TestProcessLlmResult(unittest.TestCase):
    def test_valid_response(self):
        data, raw = process_llm_result('{"options": []}')
        self.assertEqual(data, {"options": []})
        self.assertEqual(raw, '{"options": []}')

    def test_missing_options_raises_extraction_error(self):
        bad_input = '{"error": "waiting for more parts"}'
        with self.assertRaises(ExtractionError) as ctx:
            process_llm_result(bad_input)
        self.assertIn("missing 'options' key", str(ctx.exception))
        self.assertEqual(ctx.exception.raw_response, bad_input)

    def test_no_json_raises_extraction_error(self):
        with self.assertRaises(ExtractionError):
            process_llm_result("no json here")


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
        text = extract_text_from_lines(self.orig, 1, 4)
        self.assertIn("**-v**, **--verbose**", text)
        self.assertIn("Enable verbose output.", text)
        self.assertIn("Shows detailed information.", text)

    def test_flag_line_only(self):
        text = extract_text_from_lines(self.orig, 1, 1)
        self.assertEqual(text, "**-v**, **--verbose**")

    def test_strips_blockquote_prefix(self):
        orig = {
            1: "> **-n**",
            2: "> ",
            3: "> Do not output trailing newline.",
        }
        text = extract_text_from_lines(orig, 1, 3)
        self.assertIn("**-n**", text)
        self.assertIn("Do not output trailing newline.", text)
        self.assertNotIn("> ", text)

    def test_invalid_range(self):
        self.assertEqual(extract_text_from_lines(self.orig, 0, 3), "")
        self.assertEqual(extract_text_from_lines(self.orig, 5, 3), "")

    def test_skips_leading_blank_body_lines(self):
        text = extract_text_from_lines(self.orig, 1, 4)
        # Should have flag line, then \n\n, then body — no leading blank line in body
        parts = text.split("\n\n", 1)
        self.assertEqual(len(parts), 2)
        self.assertFalse(parts[1].startswith("\n"))

    def test_strips_trailing_blank_body_lines(self):
        # Range includes a trailing blank line (line 5 is "")
        text = extract_text_from_lines(self.orig, 1, 5)
        self.assertFalse(text.endswith("\n"))

    def test_strips_multiple_trailing_blank_lines(self):
        orig = {
            1: "**-o**, **--output**",
            2: "",
            3: "Write output to file.",
            4: "",
            5: "",
        }
        text = extract_text_from_lines(orig, 1, 5)
        self.assertFalse(text.endswith("\n"))
        self.assertIn("Write output to file.", text)

    def test_strips_both_leading_and_trailing_blank_lines(self):
        orig = {
            1: "**-q**, **--quiet**",
            2: "",
            3: "",
            4: "Suppress output.",
            5: "",
            6: "",
        }
        text = extract_text_from_lines(orig, 1, 6)
        parts = text.split("\n\n", 1)
        self.assertEqual(len(parts), 2)
        self.assertFalse(parts[1].startswith("\n"))
        self.assertFalse(text.endswith("\n"))
        self.assertEqual(parts[1], "Suppress output.")


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
        opt = llm_option_to_store_option(raw, self.orig)
        self.assertIsInstance(opt, models.Option)
        self.assertEqual(opt.short, ["-A"])
        self.assertEqual(opt.long, ["--catenate"])
        self.assertFalse(opt.has_argument)
        self.assertIn("**-A**, **--catenate**", opt.text)
        self.assertIn("Append files to an archive.", opt.text)

    def test_missing_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "has_argument": False}
        with self.assertRaises(ValueError):
            llm_option_to_store_option(raw, self.orig)

    def test_invalid_lines_raises(self):
        raw = {"short": ["-v"], "long": [], "has_argument": False, "lines": [10]}
        with self.assertRaises(ValueError):
            llm_option_to_store_option(raw, self.orig)

    def test_bad_short_type_raises(self):
        raw = {"short": "-v", "long": [], "has_argument": False, "lines": [10, 13]}
        with self.assertRaises(ValueError):
            llm_option_to_store_option(raw, self.orig)

    def test_nested_cmd_auto_corrects(self):
        raw = {
            "short": [],
            "long": ["--exec"],
            "has_argument": False,
            "nested_cmd": True,
            "lines": [10, 13],
        }
        opt = llm_option_to_store_option(raw, self.orig)
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
        opt = llm_option_to_store_option(raw, self.orig)
        self.assertEqual(opt.positional, "FILE")


# ---------------------------------------------------------------------------
# TestSanitizeOptionFields
# ---------------------------------------------------------------------------


class TestSanitizeOptionFields(unittest.TestCase):
    def test_argument_cleared_when_short_present(self):
        short, long, ea, arg, nc = sanitize_option_fields(
            ["-D"], [], True, "debugopts", False
        )
        self.assertIsNone(arg)

    def test_argument_cleared_when_long_present(self):
        short, long, ea, arg, nc = sanitize_option_fields(
            [], ["--type"], True, "c", False
        )
        self.assertIsNone(arg)

    def test_argument_kept_for_positional(self):
        short, long, ea, arg, nc = sanitize_option_fields([], [], False, "FILE", False)
        self.assertEqual(arg, "FILE")

    def test_nested_cmd_forces_has_argument(self):
        short, long, ea, arg, nc = sanitize_option_fields(
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
        opt = llm_option_to_store_option(raw, orig)
        self.assertIsNone(opt.positional)
        self.assertEqual(opt.short, ["-D"])


# ---------------------------------------------------------------------------
# TestDedupOptions
# ---------------------------------------------------------------------------


class TestDedupOptions(unittest.TestCase):
    def test_duplicates_keep_longest_description(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "description": "short"},
            {
                "short": ["-v"],
                "long": ["--verbose"],
                "description": "a much longer description wins",
            },
        ]
        result = dedup_options(opts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["description"], "a much longer description wins")

    def test_duplicates_same_length_keeps_one(self):
        opts = [
            {"short": ["-v"], "long": ["--verbose"], "description": "first"},
            {"short": ["-v"], "long": ["--verbose"], "description": "second"},
        ]
        result = dedup_options(opts)
        self.assertEqual(len(result), 1)

    def test_different_options_kept(self):
        opts = [
            {"short": ["-v"], "long": [], "description": "verbose"},
            {"short": ["-q"], "long": [], "description": "quiet"},
        ]
        result = dedup_options(opts)
        self.assertEqual(len(result), 2)

    def test_positional_always_kept(self):
        opts = [
            {"short": [], "long": [], "description": "FILE 1"},
            {"short": [], "long": [], "description": "FILE 2"},
        ]
        result = dedup_options(opts)
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
        result = dedup_ref_options(opts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lines"], [1, 10])


if __name__ == "__main__":
    unittest.main()
