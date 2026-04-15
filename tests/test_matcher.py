import unittest

import bashlex.errors
import bashlex.ast

from explainshell import help_constants, matcher, errors
from tests import helpers

s = helpers.create_test_store()
MR = matcher.MatchResult


class test_matcher(unittest.TestCase):
    def assertMatchSingle(self, what, expectedmanpage, expectedresults):
        m = matcher.Matcher(what, s)
        groups = m.match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[1].manpage, expectedmanpage)
        self.assertEqual(groups[1].results, expectedresults)

    def test_unknown_prog(self):
        self.assertRaises(errors.ProgramDoesNotExist, matcher.Matcher("foo", s).match)

    def test_unicode(self):
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 13, "-b <arg> desc", "-b uni\u05e7\u05d5\u05d3"),
        ]

        self.assertMatchSingle(
            "bar -b uni\u05e7\u05d5\u05d3", s.find_man_page("bar")[0], matchedresult
        )

    def test_no_options(self):
        matchedresult = [MR(0, 3, "bar synopsis", "bar")]
        self.assertMatchSingle("bar", s.find_man_page("bar")[0], matchedresult)

    def test_known_arg(self):
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 10, "-a desc", "-a --a"),
            MR(11, 13, "-? help text", "-?"),
        ]

        self.assertMatchSingle(
            "bar -a --a -?", s.find_man_page("bar")[0], matchedresult
        )

    def test_arg_in_fuzzy_with_expected_value(self):
        cmd = "baz -ab arg"
        matchedresult = [
            MR(0, 3, "baz synopsis", "baz"),
            MR(4, 6, "-a desc", "-a"),
            MR(6, 11, "-b <arg> desc", "b arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("baz")[0], matchedresult)

        cmd = "baz -ab12"
        matchedresult = [
            MR(0, 3, "baz synopsis", "baz"),
            MR(4, 6, "-a desc", "-a"),
            MR(6, 9, "-b <arg> desc", "b12"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("baz")[0], matchedresult)

    def test_dashless_opts_with_arguments(self):
        cmd = "withargs arg"
        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 12, "FILE argument", "arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withargs")[0], matchedresult)

    def test_multiple_positionals(self):
        """each positional arg gets its own help text in definition order"""
        cmd = "withmultipos src.txt dst.txt"
        matchedresult = [
            MR(0, 12, "withmultipos synopsis", "withmultipos"),
            MR(13, 20, "source file(s) to copy", "src.txt"),
            MR(21, 28, "destination path", "dst.txt"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withmultipos")[0], matchedresult)

    def test_multiple_positionals_variadic(self):
        """extra args beyond the defined positionals reuse the last one;
        adjacent matches with the same text get merged"""
        cmd = "withmultipos a b c"
        matchedresult = [
            MR(0, 12, "withmultipos synopsis", "withmultipos"),
            MR(13, 14, "source file(s) to copy", "a"),
            MR(15, 18, "destination path", "b c"),  # b and c merged
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withmultipos")[0], matchedresult)

    def test_positional_name_match(self):
        """a word that exactly matches a positional key uses that key's text"""
        cmd = "withmultipos DEST"
        matchedresult = [
            MR(0, 12, "withmultipos synopsis", "withmultipos"),
            MR(13, 17, "destination path", "DEST"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withmultipos")[0], matchedresult)

    def test_reset_current_option_if_argument_taken(self):
        cmd = "withargs -ab12 arg"
        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 11, "-a desc", "-a"),
            MR(11, 14, "-b <arg> desc", "b12"),
            MR(15, 18, "FILE argument", "arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withargs")[0], matchedresult)

        cmd = "withargs -b12 arg"
        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 13, "-b <arg> desc", "-b12"),
            MR(14, 17, "FILE argument", "arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withargs")[0], matchedresult)

        # here we reset it implicitly by looking up '12'
        cmd = "withargs -b 12 arg"
        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 14, "-b <arg> desc", "-b 12"),
            MR(15, 18, "FILE argument", "arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withargs")[0], matchedresult)

    def test_arg_with_expected_value(self):
        cmd = "bar -b arg --b arg"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 18, "-b <arg> desc", "-b arg --b arg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_arg_with_expected_value_from_list(self):
        cmd = "bar -c one"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 10, "-c=one,two\ndesc", "-c one"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

        cmd = "bar -c notinlist"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, "-c=one,two\ndesc", "-c"),
            MR(7, 16, None, "notinlist"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_arg_with_expected_value_clash(self):
        """the first option expects an arg but the arg is actually an option"""
        cmd = "bar -b -a"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, "-b <arg> desc", "-b"),
            MR(7, 9, "-a desc", "-a"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_arg_with_expected_value_no_clash(self):
        """the first option expects an arg but the arg is not an option even though
        it looks like one"""
        cmd = "bar -b -xa"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, "-b <arg> desc", "-b"),
            MR(7, 9, None, "-x"),
            MR(9, 10, "-a desc", "a"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_quoted_dash_arg_is_not_split(self):
        """a quoted word like '-foo' should be treated as an argument to the
        previous option, not split as short option flags"""
        cmd = "bar -b '-foo'"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 13, "-b <arg> desc", "-b '-foo'"),
        ]
        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_quoted_dash_arg_double_quotes(self):
        """same as above but with double quotes"""
        cmd = 'bar -b "-foo"'
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 13, "-b <arg> desc", '-b "-foo"'),
        ]
        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_quoted_dash_arg_with_spaces(self):
        """a quoted word with spaces and a leading dash is an argument"""
        cmd = "bar -b '-7 days'"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 16, "-b <arg> desc", "-b '-7 days'"),
        ]
        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_unknown_arg(self):
        matchedresult = [MR(0, 3, "bar synopsis", "bar"), MR(4, 6, None, "-x")]
        self.assertMatchSingle("bar -x", s.find_man_page("bar")[0], matchedresult)

        # merges
        matchedresult = [MR(0, 3, "bar synopsis", "bar"), MR(4, 10, None, "-x --x")]
        self.assertMatchSingle("bar -x --x", s.find_man_page("bar")[0], matchedresult)

        matchedresult = [MR(0, 3, "bar synopsis", "bar"), MR(4, 8, None, "-xyz")]
        self.assertMatchSingle("bar -xyz", s.find_man_page("bar")[0], matchedresult)

        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, None, "-x"),
            MR(6, 7, "-a desc", "a"),
            MR(7, 8, None, "z"),
        ]

        self.assertMatchSingle("bar -xaz", s.find_man_page("bar")[0], matchedresult)

    def test_merge_same_match(self):
        matchedresult = [MR(0, 3, "bar synopsis", "bar"), MR(4, 8, "-a desc", "-aaa")]
        self.assertMatchSingle("bar -aaa", s.find_man_page("bar")[0], matchedresult)

    def test_known_and_unknown_arg(self):
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, "-a desc", "-a"),
            MR(7, 9, None, "-x"),
        ]
        self.assertMatchSingle("bar -a -x", s.find_man_page("bar")[0], matchedresult)

        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 6, "-a desc", "-a"),
            MR(6, 7, None, "x"),
        ]
        self.assertMatchSingle("bar -ax", s.find_man_page("bar")[0], matchedresult)

    def test_long(self):
        cmd = "bar --b=b foo"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 9, "-b <arg> desc", "--b=b"),
            MR(10, 13, None, "foo"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_arg_no_dash(self):
        cmd = "baz ab -x"
        matchedresult = [
            MR(0, 3, "baz synopsis", "baz"),
            MR(4, 5, "-a desc", "a"),
            MR(5, 6, "-b <arg> desc", "b"),
            MR(7, 9, None, "-x"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("baz")[0], matchedresult)

    def test_subcommands(self):
        cmd = "bar baz --b foo"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 7, None, "baz"),
            MR(8, 15, "-b <arg> desc", "--b foo"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

        cmd = "bar foo --b foo"
        matchedresult = [
            MR(0, 7, "bar foo synopsis", "bar foo"),
            MR(8, 15, "-b <arg> desc", "--b foo"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar foo")[0], matchedresult)

    def test_multiple_matches(self):
        cmd = "dup -ab"
        matchedresult = [
            MR(0, 3, "dup1 synopsis", "dup"),
            MR(4, 6, "-a desc", "-a"),
            MR(6, 7, "-b <arg> desc", "b"),
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(groups[1].results, matchedresult)
        self.assertEqual(groups[1].suggestions[0].source, "ubuntu/26.04/2/dup.2.gz")

    def test_arguments(self):
        cmd = "withargs -x -b freearg freearg"
        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            # tokens that look like options are still unknown
            MR(9, 11, None, "-x"),
            MR(12, 22, "-b <arg> desc", "-b freearg"),
            MR(23, 30, "FILE argument", "freearg"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("withargs")[0], matchedresult)

    def test_arg_is_dash(self):
        cmd = "bar -b - -a -"
        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 8, "-b <arg> desc", "-b -"),
            MR(9, 11, "-a desc", "-a"),
            MR(12, 13, None, "-"),
        ]

        self.assertMatchSingle(cmd, s.find_man_page("bar")[0], matchedresult)

    def test_nested_command(self):
        cmd = "withargs -b arg bar -a unknown"

        matchedresult = [
            [
                MR(0, 8, "withargs synopsis", "withargs"),
                MR(9, 15, "-b <arg> desc", "-b arg"),
            ],
            [
                MR(16, 19, "bar synopsis", "bar"),
                MR(20, 22, "-a desc", "-a"),
                MR(23, 30, None, "unknown"),
            ],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])

    def test_nested_option(self):
        cmd = "withargs -b arg -exec bar -a EOF -b arg"

        matchedresult = [
            [
                MR(0, 8, "withargs synopsis", "withargs"),
                MR(9, 15, "-b <arg> desc", "-b arg"),
                MR(16, 21, "-exec nest", "-exec"),
                MR(29, 32, "-exec nest", "EOF"),
                MR(33, 39, "-b <arg> desc", "-b arg"),
            ],
            [MR(22, 25, "bar synopsis", "bar"), MR(26, 28, "-a desc", "-a")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])

        cmd = "withargs -b arg -exec bar -a ';' -a"

        matchedresult = [
            [
                MR(0, 8, "withargs synopsis", "withargs"),
                MR(9, 15, "-b <arg> desc", "-b arg"),
                MR(16, 21, "-exec nest", "-exec"),
                MR(29, 32, "-exec nest", "';'"),
                MR(33, 35, "-a desc", "-a"),
            ],
            [MR(22, 25, "bar synopsis", "bar"), MR(26, 28, "-a desc", "-a")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])

        cmd = "withargs -b arg -exec bar -a \\; -a"

        matchedresult = [
            [
                MR(0, 8, "withargs synopsis", "withargs"),
                MR(9, 15, "-b <arg> desc", "-b arg"),
                MR(16, 21, "-exec nest", "-exec"),
                MR(29, 31, "-exec nest", "\\;"),
                MR(32, 34, "-a desc", "-a"),
            ],
            [MR(22, 25, "bar synopsis", "bar"), MR(26, 28, "-a desc", "-a")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])

        cmd = "withargs -exec bar -a -u"

        matchedresult = [
            [
                MR(0, 8, "withargs synopsis", "withargs"),
                MR(9, 14, "-exec nest", "-exec"),
            ],
            [
                MR(15, 18, "bar synopsis", "bar"),
                MR(19, 21, "-a desc", "-a"),
                MR(22, 24, None, "-u"),
            ],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])

    def test_multiple_nests(self):
        cmd = "withargs withargs -b arg bar"

        matchedresult = [
            [MR(0, 8, "withargs synopsis", "withargs")],
            [
                MR(9, 17, "withargs synopsis", "withargs"),
                MR(18, 24, "-b <arg> desc", "-b arg"),
            ],
            [MR(25, 28, "bar synopsis", "bar")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 4)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult[0])
        self.assertEqual(groups[2].results, matchedresult[1])
        self.assertEqual(groups[3].results, matchedresult[2])

    def test_nested_command_is_unknown(self):
        cmd = "withargs -b arg unknown"

        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 15, "-b <arg> desc", "-b arg"),
            MR(16, 23, "FILE argument", "unknown"),
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult)

    def test_unparsed(self):
        cmd = "(bar; bar) c"
        self.assertRaises(bashlex.errors.ParsingError, matcher.Matcher(cmd, s).match)

    def test_known_and_unknown_program(self):
        cmd = "bar; foo arg >f; baz"
        matchedresult = [
            [
                MR(3, 4, help_constants.OPERATORS[";"], ";"),
                MR(
                    13,
                    15,
                    help_constants.REDIRECTION
                    + "\n\n"
                    + help_constants.REDIRECTION_KIND[">"],
                    ">f",
                ),
                MR(15, 16, help_constants.OPERATORS[";"], ";"),
            ],
            [MR(0, 3, "bar synopsis", "bar")],
            [MR(5, 12, None, "foo arg")],
            [MR(17, 20, "baz synopsis", "baz")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(groups[0].results, matchedresult[0])
        self.assertEqual(groups[1].results, matchedresult[1])
        self.assertEqual(groups[2].results, matchedresult[2])

    def test_pipe(self):
        cmd = "bar | baz"
        matchedresult = [
            [MR(4, 5, help_constants.PIPELINES, "|")],
            [MR(0, 3, "bar synopsis", "bar")],
            [MR(6, 9, "baz synopsis", "baz")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(groups[0].results, matchedresult[0])
        self.assertEqual(groups[1].results, matchedresult[1])

    def test_subshells(self):
        cmd = "((bar); bar)"
        matchedresult = [
            [
                MR(0, 2, help_constants._subshell, "(("),
                MR(5, 6, help_constants._subshell, ")"),
                MR(6, 7, help_constants.OPERATORS[";"], ";"),
                MR(11, 12, help_constants._subshell, ")"),
            ],
            [MR(2, 5, "bar synopsis", "bar")],
            [MR(8, 11, "bar synopsis", "bar")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(groups[0].results, matchedresult[0])
        self.assertEqual(groups[1].results, matchedresult[1])
        self.assertEqual(groups[2].results, matchedresult[2])

    def test_redirect_first_word_of_command(self):
        cmd = "2>&1"
        matchedresult = [
            MR(
                0,
                4,
                help_constants.REDIRECTION
                + "\n\n"
                + help_constants.REDIRECTION_KIND[">"],
                "2>&1",
            )
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].results, matchedresult)

        cmd = "2>&1 bar"
        matchedresult = [
            [
                MR(
                    0,
                    4,
                    help_constants.REDIRECTION
                    + "\n\n"
                    + help_constants.REDIRECTION_KIND[">"],
                    "2>&1",
                )
            ],
            [MR(5, 8, "bar synopsis", "bar")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, matchedresult[0])
        self.assertEqual(groups[1].results, matchedresult[1])

    def test_comsub(self):
        cmd = "bar $(a) -b \"b $(c) `c`\" '$(d)' >$(e) `f`"

        matchedresult = [
            MR(0, 3, "bar synopsis", "bar"),
            MR(4, 8, None, "$(a)"),
            MR(9, 24, "-b <arg> desc", '-b "b $(c) `c`"'),
            MR(25, 31, None, "'$(d)'"),
            MR(38, 41, None, "`f`"),
        ]
        shellresult = [
            MR(
                32,
                37,
                help_constants.REDIRECTION
                + "\n\n"
                + help_constants.REDIRECTION_KIND[">"],
                ">$(e)",
            )
        ]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(groups[0].results, shellresult)
        self.assertEqual(groups[1].results, matchedresult)

        # check expansions
        self.assertEqual(
            m.expansions,
            [
                (6, 7, "substitution"),
                (17, 18, "substitution"),
                (21, 22, "substitution"),
                (35, 36, "substitution"),
                (39, 40, "substitution"),
            ],
        )

    def test_comsub_as_arg(self):
        cmd = "withargs $(a) $0"

        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 16, "FILE argument", "$(a) $0"),
        ]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult)

        # check expansions
        self.assertEqual(
            m.expansions, [(11, 12, "substitution"), (14, 16, "parameter-digits")]
        )

    def test_comsub_as_first_word(self):
        cmd = "$(a) b"

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, [MR(0, 6, None, "$(a) b")])

        # check expansions
        self.assertEqual(m.expansions, [(2, 3, "substitution")])

    def test_procsub(self):
        cmd = "withargs -b <(a) >(b)"

        matchedresult = [
            MR(0, 8, "withargs synopsis", "withargs"),
            MR(9, 16, "-b <arg> desc", "-b <(a)"),
            MR(17, 21, "FILE argument", ">(b)"),
        ]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchedresult)

        # check expansions
        self.assertEqual(
            m.expansions, [(14, 15, "substitution"), (19, 20, "substitution")]
        )

    def test_if(self):
        cmd = "if bar -a; then b; fi"
        shellresults = [
            MR(0, 2, help_constants._if, "if"),
            MR(9, 15, help_constants._if, "; then"),
            MR(17, 21, help_constants._if, "; fi"),
        ]

        matchresults = [
            [MR(3, 6, "bar synopsis", "bar"), MR(7, 9, "-a desc", "-a")],
            [MR(16, 17, None, "b")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults[0])
        self.assertEqual(groups[2].results, matchresults[1])

    def test_nested_controlflows(self):
        cmd = "for a; do while bar; do baz; done; done"
        shellresults = [
            MR(0, 9, help_constants._for, "for a; do"),
            MR(10, 15, help_constants._whileuntil, "while"),
            MR(19, 23, help_constants._whileuntil, "; do"),
            MR(27, 33, help_constants._whileuntil, "; done"),
            MR(33, 39, help_constants._for, "; done"),
        ]

        matchresults = [
            [MR(16, 19, "bar synopsis", "bar")],
            [MR(24, 27, "baz synopsis", "baz")],
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults[0])
        self.assertEqual(groups[2].results, matchresults[1])

    def test_for_expansion(self):
        cmd = "for a in $(bar); do baz; done"
        shellresults = [
            MR(0, 19, help_constants._for, "for a in $(bar); do"),
            MR(23, 29, help_constants._for, "; done"),
        ]

        matchresults = [MR(20, 23, "baz synopsis", "baz")]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

        self.assertEqual(m.expansions, [(11, 14, "substitution")])

    def test_assignment_with_expansion(self):
        cmd = 'a="$1" bar'

        shellresults = [MR(0, 6, help_constants.ASSIGNMENT, 'a="$1"')]
        matchresults = [[MR(7, 10, "bar synopsis", "bar")]]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults[0])

    def test_assignment_as_first_word(self):
        cmd = "a=b bar"

        shellresults = [MR(0, 3, help_constants.ASSIGNMENT, "a=b")]
        matchresults = [MR(4, 7, "bar synopsis", "bar")]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

    def test_expansion_limit(self):
        cmd = "a $(b $(c))"
        m = matcher.Matcher(cmd, s)
        m.match()

        class depthchecker(bashlex.ast.nodevisitor):
            def __init__(self):
                self.depth = 0
                self.maxdepth = 0

            def visitnode(self, node):
                if "substitution" in node.kind:
                    self.depth += 1
                    self.maxdepth = max(self.maxdepth, self.depth)

            def visitendnode(self, node):
                if "substitution" in node.kind:
                    self.depth -= 1

        v = depthchecker()
        v.visit(m.ast)
        self.assertEqual(v.maxdepth, 1)

    def test_functions(self):
        cmd = "function a() { bar; }"
        shellresults = [
            MR(0, 14, help_constants._function, "function a() {"),
            MR(18, 19, help_constants.OPSEMICOLON, ";"),
            MR(20, 21, help_constants._function, "}"),
        ]

        matchresults = [MR(15, 18, "bar synopsis", "bar")]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

        cmd = 'function a() { bar "$(a)"; }'
        shellresults = [
            MR(0, 14, help_constants._function, "function a() {"),
            MR(25, 26, help_constants.OPSEMICOLON, ";"),
            MR(27, 28, help_constants._function, "}"),
        ]

        matchresults = [MR(15, 18, "bar synopsis", "bar"), MR(19, 25, None, '"$(a)"')]

        m = matcher.Matcher(cmd, s)
        groups = m.match()

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)
        self.assertEqual(m.expansions, [(22, 23, "substitution")])

    def test_function_reference(self):
        cmd = "function a() { bar; a b; }; a"
        shellresults = [
            MR(0, 14, help_constants._function, "function a() {"),
            MR(18, 19, help_constants.OPSEMICOLON, ";"),
            MR(20, 21, help_constants._function_call % "a", "a"),
            MR(22, 23, help_constants._functionarg % "a", "b"),
            MR(23, 24, help_constants.OPSEMICOLON, ";"),
            MR(25, 26, help_constants._function, "}"),
            MR(26, 27, help_constants.OPSEMICOLON, ";"),
            MR(28, 29, help_constants._function_call % "a", "a"),
        ]

        matchresults = [MR(15, 18, "bar synopsis", "bar")]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

        self.assertEqual(m.functions, {"a"})

    def test_comment(self):
        cmd = "bar # a comment"

        shellresults = [MR(4, 15, help_constants.COMMENT, "# a comment")]
        matchresults = [MR(0, 3, "bar synopsis", "bar")]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

        cmd = "# just a comment"

        shellresults = [MR(0, 16, help_constants.COMMENT, "# just a comment")]

        m = matcher.Matcher(cmd, s)
        groups = m.match()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].results, shellresults)

    def test_heredoc_at_eof(self):
        cmd = "bar <<EOF"

        shellresults = [
            MR(
                4,
                9,
                help_constants.REDIRECTION
                + "\n\n"
                + help_constants.REDIRECTION_KIND["<<"],
                "<<EOF",
            )
        ]

        matchresults = [MR(0, 3, "bar synopsis", "bar")]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, shellresults)
        self.assertEqual(groups[1].results, matchresults)

    def test_no_synopsis(self):
        cmd = "nosynopsis a"

        matchresults = [
            MR(0, 10, help_constants.NO_SYNOPSIS, "nosynopsis"),
            MR(11, 12, None, "a"),
        ]

        groups = matcher.Matcher(cmd, s).match()
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].results, [])
        self.assertEqual(groups[1].results, matchresults)
