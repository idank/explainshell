"""Tests for explainshell.roff_parser — direct roff-based option extraction."""

import os
import unittest

from explainshell import store
from explainshell.roff_parser import (
    _clean_roff,
    _detect_dialect,
    _parse_flag_text,
    _parse_man_options,
    _parse_mdoc_options,
    _parse_roff_args,
    _read_roff,
    parse_options,
)

_MANPAGES = os.path.join(os.path.dirname(__file__), "..", "manpages", "1")


def _gz(name):
    return os.path.join(_MANPAGES, name)


# ---------------------------------------------------------------------------
# _clean_roff
# ---------------------------------------------------------------------------

class TestCleanRoff(unittest.TestCase):
    def test_font_escapes(self):
        self.assertEqual(_clean_roff(r"\fBbold\fR"), "bold")
        self.assertEqual(_clean_roff(r"\fIitalic\fP"), "italic")

    def test_dash_escape(self):
        self.assertEqual(_clean_roff(r"\-"), "-")
        self.assertEqual(_clean_roff(r"\-\-verbose"), "--verbose")

    def test_zero_width_space(self):
        self.assertEqual(_clean_roff(r"foo\&bar"), "foobar")

    def test_backslash_escape(self):
        self.assertEqual(_clean_roff(r"\e"), "\\")

    def test_quote_escapes(self):
        self.assertEqual(_clean_roff(r"\(aq"), "'")

    def test_color_directives(self):
        self.assertEqual(_clean_roff(r"\m[blue]text\m[]"), "text")

    def test_size_changes(self):
        self.assertEqual(_clean_roff(r"\s-2small\s+2"), "small")

    def test_multiple_spaces(self):
        self.assertEqual(_clean_roff("foo   bar"), "foo bar")


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
                self.assertNotEqual(f, "exec",
                                    "Nested .IP 'exec' leaked as top-level option")
                self.assertNotEqual(f, "opt",
                                    "Nested .IP 'opt' leaked as top-level option")


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


class TestParseOptionsReturnType(unittest.TestCase):
    """parse_options() should return store.Option instances with sequential indices."""

    def test_sequential_indices(self):
        opts = parse_options(_gz("echo.1.gz"))
        for i, opt in enumerate(opts):
            self.assertEqual(opt.idx, i)

    def test_section_is_options(self):
        opts = parse_options(_gz("echo.1.gz"))
        for opt in opts:
            self.assertEqual(opt.section, "OPTIONS")

    def test_is_option_true(self):
        opts = parse_options(_gz("echo.1.gz"))
        for opt in opts:
            self.assertTrue(opt.is_option)

    def test_nested_cmd_false(self):
        """Roff parser always sets nested_cmd to False."""
        opts = parse_options(_gz("echo.1.gz"))
        for opt in opts:
            self.assertFalse(opt.nested_cmd)


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
