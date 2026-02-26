"""Tests for explainshell.tree_parser — mandoc tree-based option extraction."""

import os
import textwrap
import unittest

from explainshell.tree_parser import (
    parse_tree,
    parse_options,
)

_MANPAGES_1 = os.path.join(os.path.dirname(__file__), "..", "manpages", "1")
_MANPAGES_UBUNTU = os.path.join(os.path.dirname(__file__), "..", "manpages", "ubuntu", "25.10", "1")


def _gz1(name):
    return os.path.join(_MANPAGES_1, name)


def _gzu(name):
    return os.path.join(_MANPAGES_UBUNTU, name)


# ---------------------------------------------------------------------------
# Tree parsing
# ---------------------------------------------------------------------------

class TestParseTree(unittest.TestCase):
    def test_basic_structure(self):
        tree_text = textwrap.dedent("""\
            title = "TEST"
            sec   = "1"
            vol   = "Test Manual"

            SH (block) *1:2
              SH (head) 1:2 ID=HREF
                  NAME (text) 1:5
              SH (body) 1:2
                  test - a test (text) *2:1
            SH (block) *3:2
              SH (head) 3:2 ID=HREF
                  OPTIONS (text) 3:5
              SH (body) 3:2
                  TP (block) *4:2
                    TP (head) 4:2
                        \\-v (text) *5:1
                    TP (body) 5:1
                        Be verbose. (text) *6:1
        """)
        root = parse_tree(tree_text)
        sections = root.find("SH", "block")
        self.assertEqual(len(sections), 2)

        # First section is NAME
        name_head = sections[0].get_head()
        self.assertEqual(name_head.get_text(), "NAME")

        # Second section is OPTIONS with a TP entry
        opts_body = sections[1].get_body()
        tp_blocks = opts_body.find("TP", "block")
        self.assertEqual(len(tp_blocks), 1)

    def test_comments_skipped(self):
        tree_text = textwrap.dedent("""\
            title = "TEST"

             This is a comment (comment) 1:3
            SH (block) *2:2
              SH (head) 2:2
                  NAME (text) 2:5
              SH (body) 2:2
                  test (text) *3:1
        """)
        root = parse_tree(tree_text)
        sections = root.find("SH", "block")
        self.assertEqual(len(sections), 1)


# ---------------------------------------------------------------------------
# Integration tests with real man pages
# ---------------------------------------------------------------------------

class TestEcho(unittest.TestCase):
    """echo.1.gz — simple help2man TP-style page."""

    def setUp(self):
        path = _gz1("echo.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        # echo has -n, -e, -E, --help, --version + escape sequences
        self.assertGreaterEqual(len(self.options), 5)

    def test_has_n_flag(self):
        found = [o for o in self.options if "-n" in o.short]
        self.assertTrue(found, "Expected -n flag")

    def test_has_help_flag(self):
        found = [o for o in self.options if "--help" in o.long]
        self.assertTrue(found, "Expected --help flag")


class TestXargs(unittest.TestCase):
    """xargs.1.gz — man(7) TP-style with BI macros."""

    def setUp(self):
        path = _gzu("xargs.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 10)

    def test_has_null_flag(self):
        found = [o for o in self.options if "-0" in o.short or "--null" in o.long]
        self.assertTrue(found, "Expected -0/--null flag")

    def test_has_max_args(self):
        found = [o for o in self.options if "-n" in o.short or "--max-args" in o.long]
        self.assertTrue(found, "Expected -n/--max-args flag")

    def test_arg_file_expects_arg(self):
        found = [o for o in self.options if "-a" in o.short or "--arg-file" in o.long]
        self.assertTrue(found, "Expected -a/--arg-file flag")
        if found:
            self.assertTrue(found[0].expects_arg)


class TestBsdtar(unittest.TestCase):
    """bsdtar.1.gz — mdoc format with Bl/It entries."""

    def setUp(self):
        path = _gz1("bsdtar.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 20)

    def test_has_create_flag(self):
        found = [o for o in self.options if "-c" in o.short]
        self.assertTrue(found, "Expected -c flag")

    def test_has_file_flag(self):
        found = [o for o in self.options if "-f" in o.short]
        self.assertTrue(found, "Expected -f flag")
        if found:
            self.assertTrue(found[0].expects_arg)

    def test_has_verbose_flag(self):
        found = [o for o in self.options if "-v" in o.short]
        self.assertTrue(found, "Expected -v flag")


class TestGit(unittest.TestCase):
    """git.1.gz — DocBook PP+RS style."""

    def setUp(self):
        path = _gz1("git.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 8)

    def test_has_version_flag(self):
        found = [o for o in self.options if "--version" in o.long]
        self.assertTrue(found, "Expected --version flag")

    def test_has_help_flag(self):
        found = [o for o in self.options if "--help" in o.long]
        self.assertTrue(found, "Expected --help flag")

    def test_has_exec_path(self):
        found = [o for o in self.options if "--exec-path" in o.long]
        self.assertTrue(found, "Expected --exec-path flag")

    def test_has_paginate(self):
        found = [o for o in self.options
                 if "-p" in o.short or "--paginate" in o.long]
        self.assertTrue(found, "Expected -p/--paginate flag")


class TestCurl(unittest.TestCase):
    """curl.1.gz — man(7) IP-style with many options."""

    def setUp(self):
        path = _gzu("curl.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        # curl has ~250 options
        self.assertGreaterEqual(len(self.options), 200)

    def test_has_verbose(self):
        found = [o for o in self.options if "-v" in o.short or "--verbose" in o.long]
        self.assertTrue(found, "Expected -v/--verbose flag")


class TestFind(unittest.TestCase):
    """find.1.gz — options spread across many sections (TESTS, ACTIONS, etc.)."""

    def setUp(self):
        path = _gz1("find.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 30)

    def test_has_name_flag(self):
        found = [o for o in self.options if "-name" in o.short]
        self.assertTrue(found, "Expected -name flag")

    def test_has_type_flag(self):
        found = [o for o in self.options if "-type" in o.short]
        self.assertTrue(found, "Expected -type flag")


class TestDocker(unittest.TestCase):
    """docker.1.gz — go-md2man PP-only style with bare text first option."""

    def setUp(self):
        path = _gzu("docker.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 10)

    def test_has_help(self):
        found = [o for o in self.options if "--help" in o.long]
        self.assertTrue(found, "Expected --help flag")

    def test_has_debug(self):
        found = [o for o in self.options if "-D" in o.short or "--debug" in o.long]
        self.assertTrue(found, "Expected -D/--debug flag")


class TestFirejail(unittest.TestCase):
    """firejail.1.gz — large TP-based page with \\x1e soft hyphens."""

    def setUp(self):
        path = _gzu("firejail.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 150)

    def test_has_allowdebuggers(self):
        found = [o for o in self.options if "--allowdebuggers" in o.long or "--allow-debuggers" in o.long]
        self.assertTrue(found, "Expected --allowdebuggers flag")


class TestA68g(unittest.TestCase):
    """a68g.1.gz — mdoc page with Bl/It lists (attrs contain digits)."""

    def setUp(self):
        path = _gzu("a68g.1.gz")
        if not os.path.exists(path):
            self.skipTest(f"{path} not found")
        self.options = parse_options(path).options

    def test_option_count(self):
        self.assertGreaterEqual(len(self.options), 40)

    def test_has_help(self):
        found = [o for o in self.options if "--help" in o.long]
        self.assertTrue(found, "Expected --help flag")


if __name__ == "__main__":
    unittest.main()
