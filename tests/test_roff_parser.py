"""Tests for explainshell.roff_parser — direct roff-based option extraction."""

import os
import unittest

from explainshell import store
from explainshell.roff_parser import (
    clean_roff,
    _clean_roff_description,
    _detect_dialect,
    _parse_flag_text,
    _parse_roff_args,
    parse_options,
)

_MANPAGES = os.path.join(
    os.path.dirname(__file__), "manpages", "ubuntu", "12.04", "1"
)


def _gz(name):
    return os.path.join(_MANPAGES, name)


# ---------------------------------------------------------------------------
# _clean_roff
# ---------------------------------------------------------------------------


class TestCleanRoff(unittest.TestCase):
    def test_font_escapes(self):
        self.assertEqual(clean_roff(r"\fBbold\fR"), "bold")
        self.assertEqual(clean_roff(r"\fIitalic\fP"), "italic")

    def test_dash_escape(self):
        self.assertEqual(clean_roff(r"\-"), "-")
        self.assertEqual(clean_roff(r"\-\-verbose"), "--verbose")

    def test_zero_width_space(self):
        self.assertEqual(clean_roff(r"foo\&bar"), "foobar")

    def test_backslash_escape(self):
        self.assertEqual(clean_roff(r"\e"), "\\")

    def test_quote_escapes(self):
        self.assertEqual(clean_roff(r"\(aq"), "'")

    def test_color_directives(self):
        self.assertEqual(clean_roff(r"\m[blue]text\m[]"), "text")

    def test_size_changes(self):
        self.assertEqual(clean_roff(r"\s-2small\s+2"), "small")

    def test_multiple_spaces(self):
        self.assertEqual(clean_roff("foo   bar"), "foo bar")

    def test_backslash_space(self):
        self.assertEqual(clean_roff(r"-C\ cmdlist"), "-C cmdlist")

    def test_continuation_escape(self):
        self.assertEqual(clean_roff(r"foo\cbar"), "foobar")

    def test_zero_width_break(self):
        self.assertEqual(clean_roff(r"foo\:bar"), "foobar")

    def test_thin_space_caret(self):
        self.assertEqual(clean_roff(r"s\^|\^l"), "s|l")

    def test_thin_space_pipe(self):
        self.assertEqual(clean_roff(r"s\|l"), "sl")


# ---------------------------------------------------------------------------
# _clean_roff_description
# ---------------------------------------------------------------------------


class TestCleanRoffDescription(unittest.TestCase):
    def test_inline_B_joined_with_previous(self):
        text = "Follow symbolic links. When\n.B find\nexamines files."
        result = _clean_roff_description(text)
        self.assertEqual(result, "Follow symbolic links. When find examines files.")

    def test_inline_BR_joined(self):
        text = "implies\n.BR \\-noleaf .\nIf you later use"
        result = _clean_roff_description(text)
        self.assertEqual(result, "implies -noleaf. If you later use")

    def test_inline_I_joined(self):
        text = "the\n.I command\nis executed"
        result = _clean_roff_description(text)
        self.assertEqual(result, "the command is executed")

    def test_inline_BI_joined(self):
        text = "Use\n.BI \\-\\-file= NAME\nto specify."
        result = _clean_roff_description(text)
        self.assertEqual(result, "Use --file=NAME to specify.")

    def test_inline_macro_at_start(self):
        """Inline macro with no preceding text starts a new line."""
        text = ".B find\nsearches directories."
        result = _clean_roff_description(text)
        self.assertEqual(result, "find searches directories.")

    def test_paragraph_breaks_preserved(self):
        text = "First paragraph.\n\nSecond paragraph."
        result = _clean_roff_description(text)
        self.assertEqual(result, "First paragraph.\n\nSecond paragraph.")

    def test_consecutive_text_lines_joined(self):
        text = "This is a long\ndescription that spans\nmultiple lines."
        result = _clean_roff_description(text)
        self.assertEqual(
            result, "This is a long description that spans multiple lines."
        )

    def test_strips_if_conditional(self):
        text = "some text\n.if n \\{\\\n.\\}\nmore text"
        result = _clean_roff_description(text)
        self.assertEqual(result, "some text more text")

    def test_strips_ie_el_conditionals(self):
        text = "before\n.ie condition\n.el\nafter"
        result = _clean_roff_description(text)
        self.assertEqual(result, "before after")

    def test_strips_nr_register(self):
        text = "text\n.nr an-no-space-flag 1\nmore"
        result = _clean_roff_description(text)
        self.assertEqual(result, "text more")

    def test_strips_IP_in_description(self):
        text = "options include\n.IP exec\nShow diagnostic info\n.IP opt\nPrint info"
        result = _clean_roff_description(text)
        self.assertEqual(result, "options include Show diagnostic info Print info")

    def test_strips_TS_TE_table(self):
        text = "before\n.TS\ntab(;);\n.TE\nafter"
        result = _clean_roff_description(text)
        self.assertEqual(result, "before tab(;); after")

    def test_strips_RS_RE(self):
        text = "before\n.RS 4\nindented\n.RE\nafter"
        result = _clean_roff_description(text)
        self.assertEqual(result, "before indented after")

    def test_PP_preserves_paragraph_break(self):
        text = "first para\n.PP\nsecond para"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first para\n\nsecond para")

    def test_LP_preserves_paragraph_break(self):
        text = "first para\n.LP\nsecond para"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first para\n\nsecond para")

    def test_P_preserves_paragraph_break(self):
        text = "first para\n.P\nsecond para"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first para\n\nsecond para")

    def test_sp_preserves_paragraph_break(self):
        text = "first para\n.sp\nsecond para"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first para\n\nsecond para")

    def test_multiple_sp_collapsed(self):
        text = "first para\n.sp\n.sp\nsecond para"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first para\n\nsecond para")

    def test_strips_unknown_uppercase_macro(self):
        text = "text\n.Xx something\nmore"
        result = _clean_roff_description(text)
        self.assertEqual(result, "text more")

    def test_strips_comments(self):
        text = 'before\n.\\" a comment\nafter'
        result = _clean_roff_description(text)
        self.assertEqual(result, "before after")

    def test_strips_closing_brace(self):
        text = "before\n.\\}\nafter"
        result = _clean_roff_description(text)
        self.assertEqual(result, "before after")

    def test_font_escapes_cleaned(self):
        text = "the \\fBbold\\fR word"
        result = _clean_roff_description(text)
        self.assertEqual(result, "the bold word")

    def test_consecutive_blank_lines_collapsed(self):
        text = "first\n\n\n\nsecond"
        result = _clean_roff_description(text)
        self.assertEqual(result, "first\n\nsecond")

    def test_empty_input(self):
        self.assertEqual(_clean_roff_description(""), "")

    def test_only_macros(self):
        text = ".br\n.sp\n.PD 0"
        result = _clean_roff_description(text)
        self.assertEqual(result, "")


class TestDescriptionCleaningIntegration(unittest.TestCase):
    """Verify descriptions in real man pages are clean of roff artifacts."""

    def _all_descriptions(self, gz_name):
        opts = parse_options(_gz(gz_name))
        return [(o.short + o.long, o.text) for o in opts]

    def _assert_no_roff_macros(self, gz_name):
        for flags, desc in self._all_descriptions(gz_name):
            for line in desc.split("\n"):
                line = line.strip()
                if not line:
                    continue
                self.assertNotRegex(
                    line,
                    r"^\.[A-Z][a-z]?\b",
                    f"Roff macro leaked in {gz_name} option {flags}: {line!r}",
                )
                self.assertNotRegex(
                    line,
                    r"^\.(if|ie|el|fi|nf|br|sp|nr|mk|it|ps|ft|PD|RS|RE|PP|LP|IP|TP|HP)\b",
                    f"Roff directive leaked in {gz_name} option {flags}: {line!r}",
                )
                self.assertNotRegex(
                    line,
                    r"^\.\\\}",
                    f"Closing brace leaked in {gz_name} option {flags}: {line!r}",
                )

    def test_find_no_roff_macros(self):
        self._assert_no_roff_macros("find.1.gz")

    def test_git_no_roff_macros(self):
        self._assert_no_roff_macros("git.1.gz")

    def test_git_rebase_no_roff_macros(self):
        self._assert_no_roff_macros("git-rebase.1.gz")

    def test_xargs_no_roff_macros(self):
        self._assert_no_roff_macros("xargs.1.gz")

    def test_echo_no_roff_macros(self):
        self._assert_no_roff_macros("echo.1.gz")

    def test_tar_no_roff_macros(self):
        self._assert_no_roff_macros("tar.1.gz")

    def test_bsdtar_no_roff_macros(self):
        self._assert_no_roff_macros("bsdtar.1.gz")

    def test_find_descriptions_are_single_line_paragraphs(self):
        """Lines within a paragraph should be joined — no mid-sentence breaks.

        The first paragraph starts with the flags line followed by a newline
        and the first description paragraph, so it has at most 2 lines.
        Subsequent paragraphs should each be a single line.
        """
        for flags, desc in self._all_descriptions("find.1.gz"):
            paragraphs = desc.split("\n\n")
            for i, para in enumerate(paragraphs):
                lines = [line for line in para.split("\n") if line.strip()]
                if not lines:
                    continue
                max_expected = 2 if i == 0 else 1
                self.assertLessEqual(
                    len(lines),
                    max_expected,
                    f"Unexpected line count in find option {flags} para {i}: {para[:120]!r}",
                )


# ---------------------------------------------------------------------------
# _parse_roff_args
# ---------------------------------------------------------------------------


class TestParseRoffArgs(unittest.TestCase):
    def test_quoted_and_unquoted(self):
        self.assertEqual(_parse_roff_args('"--file=" FILE'), ["--file=", "FILE"])

    def test_quoted_with_space(self):
        self.assertEqual(_parse_roff_args('"-a " file'), ["-a ", "file"])

    def test_unquoted_only(self):
        self.assertEqual(_parse_roff_args("foo bar"), ["foo", "bar"])

    def test_backslash_space_in_unquoted(self):
        self.assertEqual(
            _parse_roff_args(r"\-C\ cmdlist"),
            [r"\-C\ cmdlist"],
        )


# ---------------------------------------------------------------------------
# _parse_flag_text
# ---------------------------------------------------------------------------


class TestParseFlagText(unittest.TestCase):
    def test_simple_short(self):
        result = _parse_flag_text("-n")
        self.assertEqual(result["short"], ["-n"])
        self.assertEqual(result["long"], [])
        self.assertFalse(result["expects_arg"])

    def test_simple_long(self):
        result = _parse_flag_text("--verbose")
        self.assertEqual(result["short"], [])
        self.assertEqual(result["long"], ["--verbose"])

    def test_short_and_long(self):
        result = _parse_flag_text("-v, --verbose")
        self.assertEqual(result["short"], ["-v"])
        self.assertEqual(result["long"], ["--verbose"])

    def test_flag_with_arg(self):
        result = _parse_flag_text("-f FILE")
        self.assertEqual(result["short"], ["-f"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "FILE")

    def test_long_with_equals_arg(self):
        result = _parse_flag_text("--file=FILE")
        self.assertEqual(result["long"], ["--file"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "FILE")

    def test_combined_short_long_with_arg(self):
        result = _parse_flag_text("-f, --file=FILE")
        self.assertEqual(result["short"], ["-f"])
        self.assertEqual(result["long"], ["--file"])
        self.assertTrue(result["expects_arg"])

    def test_roff_bold_flag(self):
        result = _parse_flag_text(r"\fB-n\fR")
        self.assertEqual(result["short"], ["-n"])

    def test_roff_long_flag(self):
        result = _parse_flag_text(r"\fB\-\-version\fR")
        self.assertEqual(result["long"], ["--version"])

    def test_optional_arg_brackets(self):
        result = _parse_flag_text("--exec-path[=<path>]")
        self.assertEqual(result["long"], ["--exec-path"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "<path>")

    def test_flag_with_angle_arg(self):
        result = _parse_flag_text("-c <name>=<value>")
        self.assertEqual(result["short"], ["-c"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "<name>=<value>")

    def test_bi_macro_flag(self):
        result = _parse_flag_text('.BI "--file=" FILE')
        self.assertEqual(result["long"], ["--file"])
        self.assertTrue(result["expects_arg"])

    def test_bi_macro_short(self):
        result = _parse_flag_text('.BI "-a " file')
        self.assertEqual(result["short"], ["-a"])
        self.assertTrue(result["expects_arg"])

    def test_b_macro(self):
        result = _parse_flag_text(r".B \-\-null")
        self.assertEqual(result["long"], ["--null"])

    def test_empty_returns_empty(self):
        result = _parse_flag_text("")
        self.assertEqual(result, {})

    def test_escape_sequence_not_flag(self):
        # Escape sequences like \e\e should not be treated as flags
        result = _parse_flag_text(r"\e\e")
        self.assertEqual(result["short"], [])
        self.assertEqual(result["long"], [])

    def test_git_style_short_long_with_arg(self):
        result = _parse_flag_text(r"-s <strategy>, --strategy=<strategy>")
        self.assertEqual(result["short"], ["-s"])
        self.assertEqual(result["long"], ["--strategy"])
        self.assertTrue(result["expects_arg"])

    def test_short_flag_with_glued_angle_arg(self):
        """Single-char flag with angle-bracket arg glued on: -C<n>"""
        result = _parse_flag_text("-C<n>")
        self.assertEqual(result["short"], ["-C"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "<n>")

    def test_multichar_flag_with_glued_angle_arg(self):
        """Multi-char flag with angle-bracket arg glued on: -lf<logfile>"""
        result = _parse_flag_text("-lf<logfile>")
        self.assertEqual(result["short"], ["-lf"])
        self.assertTrue(result["expects_arg"])
        self.assertEqual(result["argument"], "<logfile>")


# ---------------------------------------------------------------------------
# _detect_dialect
# ---------------------------------------------------------------------------


class TestDetectDialect(unittest.TestCase):
    def test_man_dialect(self):
        lines = ['.TH ECHO "1" "September 2011"\n', ".SH NAME\n"]
        self.assertEqual(_detect_dialect(lines), "man")

    def test_mdoc_dialect(self):
        lines = [".Dd Mar 31, 2012\n", ".Dt TAR 1\n", ".Sh NAME\n"]
        self.assertEqual(_detect_dialect(lines), "mdoc")

    def test_default_is_man(self):
        lines = ["some random text\n"]
        self.assertEqual(_detect_dialect(lines), "man")


# ---------------------------------------------------------------------------
# Integration tests with real man pages
# ---------------------------------------------------------------------------


class TestParseEcho(unittest.TestCase):
    """echo.1.gz — .TP pattern (help2man style)."""

    def setUp(self):
        self.opts = parse_options(_gz("echo.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 0)

    def test_returns_store_options(self):
        for opt in self.opts:
            self.assertIsInstance(opt, store.Option)

    def test_has_n_flag(self):
        flags = {f for o in self.opts for f in o.short}
        self.assertIn("-n", flags)

    def test_has_e_flag(self):
        flags = {f for o in self.opts for f in o.short}
        self.assertIn("-e", flags)

    def test_has_E_flag(self):
        flags = {f for o in self.opts for f in o.short}
        self.assertIn("-E", flags)

    def test_no_escape_sequences(self):
        """Escape sequences like \\a, \\b should not be extracted as options."""
        for opt in self.opts:
            for flag in opt.short + opt.long:
                self.assertTrue(
                    flag.startswith("-"),
                    f"Non-flag entry found: {flag}",
                )

    def test_has_descriptions(self):
        for opt in self.opts:
            self.assertTrue(
                len(opt.text) > 0,
                f"Option {opt.short + opt.long} has empty description",
            )


class TestParseTar(unittest.TestCase):
    """tar.1.gz — mdoc .It Fl pattern."""

    def setUp(self):
        self.opts = parse_options(_gz("tar.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 10)

    def test_has_create(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--create", longs)

    def test_has_extract(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--extract", longs)

    def test_has_short_c(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-c", shorts)

    def test_has_short_f(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-f", shorts)

    def test_file_expects_arg(self):
        for opt in self.opts:
            if "-f" in opt.short or "--file" in opt.long:
                self.assertTrue(opt.expects_arg)
                break
        else:
            self.fail("-f/--file option not found")

    def test_no_triple_dashes(self):
        """Flags should not have --- prefix."""
        for opt in self.opts:
            for flag in opt.short + opt.long:
                self.assertFalse(
                    flag.startswith("---"),
                    f"Triple-dash flag found: {flag}",
                )


class TestParseGit(unittest.TestCase):
    """git.1.gz — .PP + .RS/.RE pattern (DocBook)."""

    def setUp(self):
        self.opts = parse_options(_gz("git.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 5)

    def test_has_version(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--version", longs)

    def test_has_help(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--help", longs)

    def test_has_c(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-c", shorts)

    def test_c_expects_arg(self):
        for opt in self.opts:
            if "-c" in opt.short:
                self.assertTrue(opt.expects_arg)
                break

    def test_exec_path_expects_arg(self):
        for opt in self.opts:
            if "--exec-path" in opt.long:
                self.assertTrue(opt.expects_arg)
                break

    def test_bare_no_arg(self):
        for opt in self.opts:
            if "--bare" in opt.long:
                self.assertFalse(opt.expects_arg)
                break


class TestParseFind(unittest.TestCase):
    """find.1.gz — .IP pattern with .SS subsections."""

    def setUp(self):
        self.opts = parse_options(_gz("find.1.gz"))

    def test_finds_many_options(self):
        self.assertGreater(len(self.opts), 20)

    def test_has_P(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-P", shorts)

    def test_has_L(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-L", shorts)

    def test_has_H(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-H", shorts)

    def test_has_name(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-name", shorts)

    def test_has_exec(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-exec", shorts)

    def test_name_expects_arg(self):
        for opt in self.opts:
            if "-name" in opt.short:
                self.assertTrue(opt.expects_arg)
                break

    def test_D_expects_arg(self):
        for opt in self.opts:
            if "-D" in opt.short:
                self.assertTrue(opt.expects_arg)
                break

    def test_nested_ip_not_top_level(self):
        """Nested .IP entries (like under -D's .RS block) should not appear as
        top-level options."""
        for opt in self.opts:
            for f in opt.short + opt.long:
                self.assertNotEqual(
                    f, "exec", "Nested .IP 'exec' leaked as top-level option"
                )
                self.assertNotEqual(
                    f, "opt", "Nested .IP 'opt' leaked as top-level option"
                )


class TestParseBsdtar(unittest.TestCase):
    """bsdtar.1.gz — mdoc .It Fl pattern."""

    def setUp(self):
        self.opts = parse_options(_gz("bsdtar.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 10)

    def test_has_short_c(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-c", shorts)

    def test_has_short_f(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-f", shorts)

    def test_file_expects_arg(self):
        for opt in self.opts:
            if "-f" in opt.short:
                self.assertTrue(opt.expects_arg)
                break


class TestParseXargs(unittest.TestCase):
    """xargs.1.gz — .TP with .PD 0 alias grouping."""

    def setUp(self):
        self.opts = parse_options(_gz("xargs.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 10)

    def test_has_null_alias(self):
        """--null and -0 should be grouped together."""
        for opt in self.opts:
            if "-0" in opt.short:
                self.assertIn("--null", opt.long)
                break
        else:
            self.fail("-0 option not found")

    def test_has_arg_file(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--arg-file", longs)

    def test_arg_file_expects_arg(self):
        for opt in self.opts:
            if "--arg-file" in opt.long:
                self.assertTrue(opt.expects_arg)
                break

    def test_max_args_expects_arg(self):
        for opt in self.opts:
            if "--max-args" in opt.long or "-n" in opt.short:
                self.assertTrue(opt.expects_arg)
                break


class TestParseGitRebase(unittest.TestCase):
    """git-rebase.1.gz — .PP + .RS/.RE pattern."""

    def setUp(self):
        self.opts = parse_options(_gz("git-rebase.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 5)

    def test_has_continue(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--continue", longs)

    def test_has_abort(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--abort", longs)

    def test_has_merge(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--merge", longs)

    def test_has_strategy(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--strategy", longs)

    def test_strategy_expects_arg(self):
        for opt in self.opts:
            if "--strategy" in opt.long:
                self.assertTrue(opt.expects_arg)
                break

    def test_merge_has_paragraph_breaks(self):
        """--merge description has multiple paragraphs separated by .sp."""
        for opt in self.opts:
            if "--merge" in opt.long:
                paragraphs = opt.text.split("\n\n")
                self.assertGreater(
                    len(paragraphs),
                    1,
                    f"--merge description should have multiple paragraphs: {opt.text!r}",
                )
                break
        else:
            self.fail("--merge option not found")

    def test_strategy_has_paragraph_breaks(self):
        """--strategy description has multiple paragraphs separated by .sp."""
        for opt in self.opts:
            if "--strategy" in opt.long:
                paragraphs = opt.text.split("\n\n")
                self.assertGreater(
                    len(paragraphs),
                    1,
                    f"--strategy description should have multiple paragraphs: {opt.text!r}",
                )
                break
        else:
            self.fail("--strategy option not found")


class TestParseOptionsReturnType(unittest.TestCase):
    """parse_options() should return store.Option instances."""

    def test_returns_option_instances(self):
        opts = parse_options(_gz("echo.1.gz"))
        for opt in opts:
            self.assertIsInstance(opt, store.Option)

    def test_nested_cmd_false(self):
        """Roff parser always sets nested_cmd to False."""
        opts = parse_options(_gz("echo.1.gz"))
        for opt in opts:
            self.assertFalse(opt.nested_cmd)


# ---------------------------------------------------------------------------
# Regression tests for audit fixes (section-name & format-pattern expansion)
# ---------------------------------------------------------------------------


class TestParseCurl(unittest.TestCase):
    """curl.1.gz — .IP pattern in ALL OPTIONS section (section-name miss)."""

    def setUp(self):
        self.opts = parse_options(_gz("curl.1.gz"))

    def test_finds_many_options(self):
        self.assertGreater(len(self.opts), 200)

    def test_has_verbose(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--verbose", longs)

    def test_has_data(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--data", longs)

    def test_has_output(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--output", longs)


class TestParseNmap(unittest.TestCase):
    """nmap.1.gz — .PP+.RS/.RE in non-option-named sections (all-sections fallback)."""

    def setUp(self):
        self.opts = parse_options(_gz("nmap.1.gz"))

    def test_finds_many_options(self):
        self.assertGreater(len(self.opts), 50)

    def test_has_sn(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-sn", shorts)

    def test_has_ipv6(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-6", shorts)

    def test_has_exclude(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--exclude", longs)


class TestParseJq(unittest.TestCase):
    """jq.1.gz — .TP pattern in INVOKING JQ section (all-sections fallback)."""

    def setUp(self):
        self.opts = parse_options(_gz("jq.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 10)

    def test_has_null_input(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--null-input", longs)

    def test_has_raw_output(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--raw-output", longs)


class TestParseEbookConvert(unittest.TestCase):
    """ebook-convert.1.gz — .TP in *INPUT/*OUTPUT OPTIONS sections (section-name miss)."""

    def setUp(self):
        self.opts = parse_options(_gz("ebook-convert.1.gz"))

    def test_finds_many_options(self):
        self.assertGreater(len(self.opts), 200)

    def test_has_input_encoding(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--input-encoding", longs)


class TestParsePs(unittest.TestCase):
    """ps.1.gz — .TP in SIMPLE PROCESS SELECTION etc. (all-sections fallback)."""

    def setUp(self):
        self.opts = parse_options(_gz("ps.1.gz"))

    def test_finds_many_options(self):
        self.assertGreater(len(self.opts), 30)

    def test_has_A(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-A", shorts)

    def test_has_e(self):
        shorts = {f for o in self.opts for f in o.short}
        self.assertIn("-e", shorts)

    def test_no_backslash_in_flag_names(self):
        """Flag names should not contain roff backslash escapes."""
        for opt in self.opts:
            for flag in opt.short + opt.long:
                self.assertNotIn(
                    "\\",
                    flag,
                    f"Backslash found in flag name: {flag!r}",
                )

    def test_no_roff_escapes_in_descriptions(self):
        """Descriptions should not contain raw roff escapes like \\c, \\:, \\^."""
        for opt in self.opts:
            for esc in ("\\c", "\\:", "\\^", "\\|"):
                self.assertNotIn(
                    esc,
                    opt.text,
                    f"Roff escape {esc!r} found in description of {opt.short + opt.long}",
                )


class TestParseSu(unittest.TestCase):
    """su.1.gz — .sp + flag + .RS/.RE pattern (util-linux/asciidoc style)."""

    def setUp(self):
        self.opts = parse_options(_gz("su.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 5)

    def test_has_command(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--command", longs)

    def test_has_login(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--login", longs)

    def test_command_expects_arg(self):
        for opt in self.opts:
            if "--command" in opt.long:
                self.assertTrue(opt.expects_arg)
                break
        else:
            self.fail("--command not found")


class TestParseMore(unittest.TestCase):
    """more.1.gz — .sp + flag + .RS/.RE pattern (util-linux/asciidoc style)."""

    def setUp(self):
        self.opts = parse_options(_gz("more.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 5)

    def test_has_silent(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--silent", longs)


class TestParseDocker(unittest.TestCase):
    """docker.1.gz — .PP + flag + plain description (go-md2man style)."""

    def setUp(self):
        self.opts = parse_options(_gz("docker.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 5)

    def test_has_debug(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--debug", longs)

    def test_has_host(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--host", longs)


class TestParseLogger(unittest.TestCase):
    """logger.1.gz — .sp + flag + .RS/.RE pattern (util-linux/asciidoc style)."""

    def setUp(self):
        self.opts = parse_options(_gz("logger.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 10)

    def test_has_udp(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--udp", longs)

    def test_has_file(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--file", longs)


class TestParseTaskset(unittest.TestCase):
    """taskset.1.gz — .sp + flag + .RS/.RE pattern (util-linux/asciidoc style)."""

    def setUp(self):
        self.opts = parse_options(_gz("taskset.1.gz"))

    def test_finds_options(self):
        self.assertGreater(len(self.opts), 3)

    def test_has_all_tasks(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--all-tasks", longs)

    def test_has_cpu_list(self):
        longs = {f for o in self.opts for f in o.long}
        self.assertIn("--cpu-list", longs)


class TestParseOptionsEdgeCases(unittest.TestCase):
    def test_nonexistent_file(self):
        result = parse_options("/nonexistent/path.1.gz")
        self.assertEqual(result, [])

    def test_empty_lines(self):
        # Monkey-patch _read_roff to return empty
        import explainshell.roff_parser as rp

        original = rp._read_roff
        rp._read_roff = lambda gz: []
        try:
            result = parse_options("dummy.1.gz")
            self.assertEqual(result, [])
        finally:
            rp._read_roff = original


if __name__ == "__main__":
    unittest.main()
