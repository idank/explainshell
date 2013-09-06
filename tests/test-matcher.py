import unittest

from explainshell import matcher, store, errors, options

class mockstore(object):
    def __init__(self):
        sp = store.paragraph
        so = store.option
        sm = store.manpage

        p0 = sp(0, '-a desc', '', True)
        p1 = sp(1, '-b <arg> desc', '', True)
        p2 = sp(2, '-? help text', '', True)
        p3 = sp(3, '-c=one,two\ndesc', '', True)
        p4 = sp(4, 'FILE argument', '', True)
        opts = [so(p0, ['-a'], ['--a'], False),
                so(p1, ['-b'], ['--b'], '<arg>'),
                so(p2, ['-?'], [], False),
                so(p3, ['-c'], [], ['one', 'two'])]
        self.manpages = {
                'bar' : sm('bar.1.gz', 'bar', 'bar synopsis', opts, [], multicommand=True),
                'baz' : sm('baz.1.gz', 'baz', 'baz synopsis', opts, [], True),
                'bar foo' : sm('bar-foo.1.gz', 'bar-foo', 'bar foo synopsis', opts, [], True)}

        self.dup = [sm('dup.1.gz', 'dup', 'dup1 synopsis', opts, []),
                    sm('dup.2.gz', 'dup', 'dup2 synopsis', opts, [])]

        opts = list(opts)
        opts.append(so(p4, [], [], False, 'FILE'))
        self.manpages['withargs'] = sm('withargs.1.gz', 'withargs', 'withargs synopsis',
                                       opts, [], False)

    def findmanpage(self, x, section=None):
        try:
            if x == 'dup':
                return self.dup
            return [self.manpages[x]]
        except KeyError:
            raise errors.ProgramDoesNotExist(x)

s = mockstore()

class test_matcher(unittest.TestCase):
    def assertMatchSingle(self, what, expectedmanpage, expectedresults):
        m = matcher.matcher(what, s)
        groups = m.match()
        self.assertEquals(len(groups), 2)
        self.assertEquals(groups[1].manpage, expectedmanpage)
        self.assertEquals(groups[1].results, expectedresults)

    def test_unknown_prog(self):
        self.assertRaises(errors.ProgramDoesNotExist, matcher.matcher('foo', s).match)

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
        cmd = 'bar --b=b'
        matchedresult = [
            (0, 3, 'bar synopsis', 'bar'),
            (4, 9, '-b <arg> desc', '--b=b')]

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
        self.assertEquals(groups[1].others[0].source, 'dup.2.gz')

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

    def test_unparsed(self):
        cmd = '(bar; bar) c'
        matchedresult = [[(0, 1, None, '('), (4, 5, None, ';'), (9, 12, None, ') c')],
                         [(1, 4, 'bar synopsis', 'bar')],
                         [(6, 9, 'bar synopsis', 'bar')]]

        groups = matcher.matcher(cmd, s).match()
        self.assertEquals(groups[0].results, matchedresult[0])
        self.assertEquals(groups[1].results, matchedresult[1])
        self.assertEquals(groups[2].results, matchedresult[2])
