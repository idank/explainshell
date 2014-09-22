import unittest

import bashlex.errors

from explainshell import matcher, errors, helpconstants
from tests import helpers

s = helpers.mockstore()

class test_matcher(unittest.TestCase):
    def assertMatchSingle(self, what, expectedmanpage, expectedresults):
        m = matcher.matcher(what, s)
        groups = m.match()
        self.assertEquals(len(groups), 2)
        self.assertEquals(groups[1].manpage, expectedmanpage)
        self.assertEquals(groups[1].results, expectedresults)

    def test_unknown_prog(self):
        self.assertRaises(errors.ProgramDoesNotExist, matcher.matcher('foo', s).match)

    def test_unicode(self):
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 13, '-b <arg> desc', '-b uni???')]

        self.assertMatchSingle(u'bar -b uni\u05e7\u05d5\u05d3', s.findmanpage('bar')[0], matchedresult)

    def test_no_options(self):
        matchedresult = [(0, 3, 'bar synopsis', 'bar')]
        self.assertMatchSingle('bar', s.findmanpage('bar')[0], matchedresult)

    def test_known_arg(self):
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 10, '-a desc', '-a --a'),
            (11, 13, '-? help text', '-?')]

        self.assertMatchSingle('bar -a --a -?', s.findmanpage('bar')[0], matchedresult)

    def test_arg_in_fuzzy_with_expected_value(self):
        cmd = 'baz -ab arg'
        matchedresult = [
            (0, 3, 'baz synopsis', 'baz'),
            (4, 6, '-a desc', '-a'),
            (6, 11, '-b <arg> desc', 'b arg')]

        self.assertMatchSingle(cmd, s.findmanpage('baz')[0], matchedresult)

        cmd = 'baz -ab12'
        matchedresult = [
            (0, 3, 'baz synopsis', 'baz'),
            (4, 6, '-a desc', '-a'),
            (6, 9, '-b <arg> desc', 'b12')]

        self.assertMatchSingle(cmd, s.findmanpage('baz')[0], matchedresult)

    def test_partialmatch_with_arguments(self):
        cmd = 'withargs arg'
        matchedresult = [
            (0, 8, 'withargs synopsis', 'withargs'),
            (9, 12, 'FILE argument', 'arg')]

        self.assertMatchSingle(cmd, s.findmanpage('withargs')[0], matchedresult)

    def test_reset_current_option_if_argument_taken(self):
        cmd = 'withargs -ab12 arg'
        matchedresult = [
            (0, 8, 'withargs synopsis', 'withargs'),
            (9, 11, '-a desc', '-a'),
            (11, 14, '-b <arg> desc', 'b12'),
            (15, 18, 'FILE argument', 'arg')]

        self.assertMatchSingle(cmd, s.findmanpage('withargs')[0], matchedresult)

        cmd = 'withargs -b12 arg'
        matchedresult = [
            (0, 8, 'withargs synopsis', 'withargs'),
            (9, 13, '-b <arg> desc', '-b12'),
            (14, 17, 'FILE argument', 'arg')]

        self.assertMatchSingle(cmd, s.findmanpage('withargs')[0], matchedresult)

        # here we reset it implicitly by looking up '12'
        cmd = 'withargs -b 12 arg'
        matchedresult = [
            (0, 8, 'withargs synopsis', 'withargs'),
            (9, 14, '-b <arg> desc', '-b 12'),
            (15, 18, 'FILE argument', 'arg')]

        self.assertMatchSingle(cmd, s.findmanpage('withargs')[0], matchedresult)

    def test_arg_with_expected_value(self):
        cmd = 'bar -b arg --b arg'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 18, '-b <arg> desc', '-b arg --b arg')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_arg_with_expected_value_from_list(self):
        cmd = 'bar -c one'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 10, '-c=one,two\ndesc', '-c one')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

        cmd = 'bar -c notinlist'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 6, '-c=one,two\ndesc', '-c'),
            (7, 16, None, 'notinlist')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_arg_with_expected_value_clash(self):
        '''the first option expects an arg but the arg is actually an option'''
        cmd = 'bar -b -a'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 6, '-b <arg> desc', '-b'),
            (7, 9, '-a desc', '-a')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_arg_with_expected_value_no_clash(self):
        '''the first option expects an arg but the arg is not an option even though
        it looks like one'''
        cmd = 'bar -b -xa'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 6, '-b <arg> desc', '-b'),
            (7, 9, None, '-x'),
            (9, 10, '-a desc', 'a')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_unknown_arg(self):
        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 6, None, '-x')]
        self.assertMatchSingle('bar -x', s.findmanpage('bar')[0], matchedresult)

        # merges
        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 10, None, '-x --x')]
        self.assertMatchSingle('bar -x --x', s.findmanpage('bar')[0], matchedresult)

        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 8, None, '-xyz')]
        self.assertMatchSingle('bar -xyz', s.findmanpage('bar')[0], matchedresult)

        matchedresult = [(0, 3, 'bar synopsis', 'bar'),
                                 (4, 6, None, '-x'),
                                 (6, 7, '-a desc', 'a'), (7, 8, None, 'z')]

        self.assertMatchSingle('bar -xaz', s.findmanpage('bar')[0], matchedresult)

    def test_merge_same_match(self):
        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 8, '-a desc', '-aaa')]
        self.assertMatchSingle('bar -aaa', s.findmanpage('bar')[0], matchedresult)

    def test_known_and_unknown_arg(self):
        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 6, '-a desc', '-a'), (7, 9, None, '-x')]
        self.assertMatchSingle('bar -a -x', s.findmanpage('bar')[0], matchedresult)

        matchedresult = [(0, 3, 'bar synopsis', 'bar'), (4, 6, '-a desc', '-a'), (6, 7, None, 'x')]
        self.assertMatchSingle('bar -ax', s.findmanpage('bar')[0], matchedresult)

    def test_long(self):
        cmd = 'bar --b=b foo'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 9, '-b <arg> desc', '--b=b'),
            (10, 13, None, 'foo')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_arg_no_dash(self):
        cmd = 'baz ab -x'
        matchedresult = [
            (0, 3, 'baz synopsis', 'baz'),
            (4, 5, '-a desc', 'a'),
            (5, 6, '-b <arg> desc', 'b'),
            (7, 9, None, '-x')]

        self.assertMatchSingle(cmd, s.findmanpage('baz')[0], matchedresult)

    def test_multicommand(self):
        cmd = 'bar baz --b foo'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 7, None, 'baz'),
            (8, 15, '-b <arg> desc', '--b foo')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

        cmd = 'bar foo --b foo'
        matchedresult = [
            (0, 7, 'bar foo synopsis', 'bar foo'),
            (8, 15, '-b <arg> desc', '--b foo')]

        self.assertMatchSingle(cmd, s.findmanpage('bar foo')[0], matchedresult)

    def test_multiple_matches(self):
        cmd = 'dup -ab'
        matchedresult = [
            (0, 3, 'dup1 synopsis', 'dup'),
            (4, 6, '-a desc', '-a'),
            (6, 7, '-b <arg> desc', 'b')]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(groups[1].results, matchedresult)
        self.assertEquals(groups[1].suggestions[0].source, 'dup.2.gz')

    def test_arguments(self):
        cmd = 'withargs -x -b freearg freearg'
        matchedresult = [
            (0, 8, 'withargs synopsis', 'withargs'),
            # tokens that look like options are still unknown
            (9, 11, None, '-x'),
            (12, 22, '-b <arg> desc', '-b freearg'),
            (23, 30, 'FILE argument', 'freearg')]

        self.assertMatchSingle(cmd, s.findmanpage('withargs')[0], matchedresult)

    def test_arg_is_dash(self):
        cmd = 'bar -b - -a -'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 8, '-b <arg> desc', '-b -'),
            (9, 11, '-a desc', '-a'),
            (12, 13, None, '-')]

        self.assertMatchSingle(cmd, s.findmanpage('bar')[0], matchedresult)

    def test_nested_command(self):
        cmd = 'withargs -b arg bar -a unknown'

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 15, '-b <arg> desc', '-b arg')],
                         [(16, 19, 'bar synopsis', 'bar'),
                          (20, 22, '-a desc', '-a'),
                          (23, 30, None, 'unknown')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 3)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])

    def test_nested_option(self):
        cmd = 'withargs -b arg -exec bar -a EOF -b arg'

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 15, '-b <arg> desc', '-b arg'),
                          (16, 21, '-exec nest', '-exec'),
                          (29, 32, '-exec nest', 'EOF'),
                          (33, 39, '-b <arg> desc', '-b arg')],
                         [(22, 25, 'bar synopsis', 'bar'),
                          (26, 28, '-a desc', '-a')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 3)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])

        cmd = "withargs -b arg -exec bar -a ';' -a"

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 15, '-b <arg> desc', '-b arg'),
                          (16, 21, '-exec nest', '-exec'),
                          (29, 32, '-exec nest', "';'"),
                          (33, 35, '-a desc', '-a')],
                         [(22, 25, 'bar synopsis', 'bar'),
                          (26, 28, '-a desc', '-a')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 3)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])

        cmd = "withargs -b arg -exec bar -a \\; -a"

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 15, '-b <arg> desc', '-b arg'),
                          (16, 21, '-exec nest', '-exec'),
                          (29, 31, '-exec nest', "\\;"),
                          (32, 34, '-a desc', '-a')],
                         [(22, 25, 'bar synopsis', 'bar'),
                          (26, 28, '-a desc', '-a')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 3)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])

        cmd = 'withargs -exec bar -a -u'

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 14, '-exec nest', '-exec')],
                         [(15, 18, 'bar synopsis', 'bar'),
                          (19, 21, '-a desc', '-a'),
                          (22, 24, None, '-u')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 3)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])

    def test_multiple_nests(self):
        cmd = 'withargs withargs -b arg bar'

        matchedresult = [[(0, 8, 'withargs synopsis', 'withargs')],
                         [(9, 17, 'withargs synopsis', 'withargs'),
                          (18, 24, '-b <arg> desc', '-b arg')],
                         [(25, 28, 'bar synopsis', 'bar')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 4)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult[0])
        self.assertEquals(groups[2].results, matchedresult[1])
        self.assertEquals(groups[3].results, matchedresult[2])

    def test_nested_command_is_unknown(self):
        cmd = 'withargs -b arg unknown'

        matchedresult = [(0, 8, 'withargs synopsis', 'withargs'),
                          (9, 15, '-b <arg> desc', '-b arg'),
                          (16, 23, 'FILE argument', 'unknown')]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 2)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult)

    def test_unparsed(self):
        cmd = '(bar; bar) c'
        self.assertRaises(bashlex.errors.ParsingError,
                          matcher.matcher(cmd, s).match)


    def test_known_and_unknown_program(self):
        cmd = 'bar; foo arg >f; baz'
        matchedresult = [[(3, 4, helpconstants.OPERATORS[';'], ';'),
                          (13, 15,helpconstants.REDIRECTION + '\n\n' +
                                  helpconstants.REDIRECTION_KIND['>'], '>f'),
                          (15, 16, helpconstants.OPERATORS[';'], ';')],
                         [(0, 3, 'bar synopsis', 'bar')],
                         [(5, 12, None, 'foo arg')],
                         [(17, 20, 'baz synopsis', 'baz')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(groups[0].results, matchedresult[0])
        self.assertEquals(groups[1].results, matchedresult[1])
        self.assertEquals(groups[2].results, matchedresult[2])

    def test_pipe(self):
        cmd = 'bar | baz'
        matchedresult = [[(4, 5, helpconstants.PIPELINES, '|')],
                         [(0, 3, 'bar synopsis', 'bar')],
                         [(6, 9, 'baz synopsis', 'baz')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(groups[0].results, matchedresult[0])
        self.assertEquals(groups[1].results, matchedresult[1])

    def test_subshells(self):
        cmd = '((bar); bar)'
        matchedresult = [[(0, 2, helpconstants._subshell, '(('),
                          (5, 6, helpconstants._subshell, ')'),
                          (6, 7, helpconstants.OPERATORS[';'], ';'),
                          (11, 12, helpconstants._subshell, ')')],
                         [(2, 5, 'bar synopsis', 'bar')],
                         [(8, 11, 'bar synopsis', 'bar')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(groups[0].results, matchedresult[0])
        self.assertEquals(groups[1].results, matchedresult[1])
        self.assertEquals(groups[2].results, matchedresult[2])

    def test_redirect_first_word_of_command(self):
        cmd = '2>&1'
        matchedresult = [(0, 4, helpconstants.REDIRECTION + '\n\n' +
                                helpconstants.REDIRECTION_KIND['>'], '2>&1')]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 1)
        self.assertEquals(groups[0].results, matchedresult)

        cmd = '2>&1 bar'
        matchedresult = [[(0, 4, helpconstants.REDIRECTION + '\n\n' +
                                 helpconstants.REDIRECTION_KIND['>'], '2>&1')],
                         [(5, 8, 'bar synopsis', 'bar')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(len(groups), 2)
        self.assertEquals(groups[0].results, matchedresult[0])
        self.assertEquals(groups[1].results, matchedresult[1])

    def test_comsub(self):
        cmd = 'bar $(a) -b "b $(c) `c`" \'$(d)\' >$(e) `f`'

        matchedresult = [(0, 3, 'bar synopsis', 'bar'),
                         (4, 8, None, '$(a)'),
                         (9, 24, '-b <arg> desc', '-b "b $(c) `c`"'),
                         (25, 31, None, "'$(d)'"),
                         (38, 41, None, '`f`')]
        shellresult = [(32, 37, helpconstants.REDIRECTION + '\n\n' +
                                helpconstants.REDIRECTION_KIND['>'], '>$(e)')]

        m = matcher.matcher(cmd, s)
        groups = m.match()
        self.assertEquals(groups[0].results, shellresult)
        self.assertEquals(groups[1].results, matchedresult)

        # check expansions
        self.assertEquals(m.expansions, [(6, 7, None),
                                         (17, 18, None),
                                         (21, 22, None),
                                         (35, 36, None),
                                         (39, 40, None)])

    def test_comsub_as_arg(self):
        cmd = 'withargs $(a)'

        matchedresult = [(0, 8, 'withargs synopsis', 'withargs'),
                         (9, 13, 'FILE argument', '$(a)')]

        m = matcher.matcher(cmd, s)
        groups = m.match()
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, matchedresult)

        # check expansions
        self.assertEquals(m.expansions, [(11, 12, None)])

    def test_comsub_as_first_word(self):
        cmd = '$(a) b'

        m = matcher.matcher(cmd, s)
        groups = m.match()
        self.assertEquals(len(groups), 2)
        self.assertEquals(groups[0].results, [])
        self.assertEquals(groups[1].results, [(0, 6, None, '$(a) b')])

        # check expansions
        self.assertEquals(m.expansions, [(2, 3, None)])
