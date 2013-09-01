import unittest

from explainshell import options, store, errors

ts = options.tokenstate

class test_options(unittest.TestCase):
    def test_tokenize(self):
        s = 'bar -x'
        t = list(options.tokenize(s))
        expected = [ts(0, 3, 'bar'), ts(4, 6, '-x')]
        self.assertTokensEquals(s, t, expected, ('bar', '-x'))

        s = 'wx    y =z '
        t = list(options.tokenize(s))
        expected = [ts(0, 2, 'wx'), ts(6, 7, 'y'), ts(8, 10, '=z')]
        self.assertTokensEquals(s, t, expected, ('wx', 'y', '=z'))

        s = "a 'b' c"
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 5, 'b'), ts(6, 7, 'c')]
        self.assertTokensEquals(s, t, expected, ('a', "'b'", 'c'))

        s = "a 'b  ' c"
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 7, 'b  '), ts(8, 9, 'c')]
        self.assertTokensEquals(s, t, expected, ('a', "'b  '", 'c'))

    def assertTokensEquals(self, s, got, expected, substrings):
        self.assertEquals(got, expected)
        for (s_, e, t), ss in zip(got, substrings):
            self.assertEquals(s[s_:e], ss)

    def test_tokenize_equals(self):
        s = 'a b=c'
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 3, 'b'), ts(3, 5, '=c')]
        self.assertTokensEquals(s, t, expected, ('a', 'b', '=c'))

        s = 'a b =c'
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 3, 'b'), ts(4, 6, '=c')]
        self.assertTokensEquals(s, t, expected, ('a', 'b', '=c'))

        s = 'a b= c'
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 3, 'b'), ts(3, 4, '='), ts(5, 6, 'c')]
        self.assertTokensEquals(s, t, expected, ('a', 'b', '=', 'c'))

        s = 'a b = c'
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 3, 'b'), ts(4, 5, '='), ts(6, 7, 'c')]
        self.assertTokensEquals(s, t, expected, ('a', 'b', '=', 'c'))

        s = 'a b  = c'
        t = list(options.tokenize(s))
        expected = [ts(0, 1, 'a'), ts(2, 3, 'b'), ts(5, 6, '='), ts(7, 8, 'c')]
        self.assertTokensEquals(s, t, expected, ('a', 'b', '=', 'c'))

    def test_simple(self):
        s = '\t-a description'
        self.assertEquals(options.extract_option(s), (['-a'], []))

        s = '\t-a, description'
        self.assertEquals(options.extract_option(s), (['-a'], []))

        r = (['-a', '-b'], [])
        s = '\t-a, -b description'
        self.assertEquals(options.extract_option(s), r)

        s = '\t-a/-b description'
        self.assertEquals(options.extract_option(s), r)

        s = '\t-a -b description'
        self.assertEquals(options.extract_option(s), r)

        s = '\t-a     -b,-c,           -d description'
        self.assertEquals(options.extract_option(s), (['-a', '-b', '-c', '-d'], []))

        s = '\t--a, -b, --c-d description'
        self.assertEquals(options.extract_option(s), (['-b'], ['--a', '--c-d']))

        s = '---c-d '
        self.assertEquals(options.extract_option(s), ([], []))

        s = '-c- '
        self.assertEquals(options.extract_option(s), ([], []))

    def test_option_arg(self):
        s = '\t-a FOO, -b=BAR, description'
        self.assertEquals(options.extract_option(s),
                          ([('-a', 'FOO'), ('-b', 'BAR')], []))

        s = '\t-a [FOO], -b[=BAR], description'
        self.assertEquals(options.extract_option(s),
                          ([('-a', 'FOO'), ('-b', 'BAR')], []))

        s = '\t-a<n>, -b=<BAR>, -C <ah>'
        self.assertEquals(options.extract_option(s),
                          ([('-a', 'n'), ('-b', 'BAR'), ('-C', 'ah')], []))

        s = '\t--aa    FOO, --bb=BAR, description'
        self.assertEquals(options.extract_option(s),
                          ([], [('--aa', 'FOO'), ('--bb', 'BAR')]))

        s = '-a or -b'
        self.assertEquals(options.extract_option(s),
                          (['-a', '-b'], []))

    def test_pipe_separator(self):
        s = '-a|b'
        self.assertEquals(options.extract_option(s),
                          (['-a', 'b'], []))

        s = '-a|-b|--c|d'
        self.assertEquals(options.extract_option(s),
                          (['-a', '-b', 'd'], ['--c']))

    def test_multiline_options(self):
        s = '\t-a, -b, \n-c, --ddd description'
        self.assertEquals(options.extract_option(s),
                          (['-a', '-b', '-c'], ['--ddd']))

    def test_multiline_desc(self):
        s = '\t-a, -b description\n\tmultiline\n  another line'
        self.assertEquals(options.extract_option(s), (['-a', '-b'], []))

    def test_not_an_option(self):
        self.assertEquals(options.extract_option('foobar'), ([], []))

    def test_no_hyphen(self):
        s = '\ta=b description'
        self.assertEquals(options.extract_option(s), ([], [('a', 'b')]))

    def test_hyphen_in_arg(self):
        s = '-a=FOO-BAR, --aa=FOO-BAR'
        self.assertEquals(options.extract_option(s),
                          ([('-a', 'FOO-BAR')], [('--aa', 'FOO-BAR')]))

        #s = '-a FOO-BAR, --aa FOO-BAR'
        #self.assertEquals(options.extract_option(s),
        #                  ([('-a', 'FOO-BAR')], [('--aa', 'FOO-BAR')]))

    def test_extract(self):
        p1 = store.paragraph(0, '<b>--test</b>=<u>arg</u>\ndesc', '', True)
        p2 = store.paragraph(1, 'no options here', '', True)
        p3 = store.paragraph(2, '--foo-bar=&lt;arg&gt;\ndesc', '', True)

        m = store.manpage('', '', '', [p1, p2, p3], [])
        options.extract(m)
        r = m.options
        self.assertEquals(len(r), 2)
        self.assertEquals(r[0].text, p1.text)
        self.assertEquals(r[0].short, [])
        self.assertEquals(r[0].long, ['--test'])
        self.assertEquals(r[0].expectsarg, True)

        self.assertEquals(r[1].text, p3.text)
        self.assertEquals(r[1].short, [])
        self.assertEquals(r[1].long, ['--foo-bar'])
        self.assertEquals(r[1].expectsarg, True)

    def test_help(self):
        s = '\t-?, --help description'
        self.assertEquals(options.extract_option(s), (['-?'], ['--help']))

    def test_parsing_error(self):
        s = 'no escaped character\\'
        message = r'No escaped character \(position 21, ...cter\\\)'
        with self.assertRaisesRegexp(errors.ParsingError, message):
            list(options.tokenize(s))

        s = 'no closing "quotation'
        message = r'No closing quotation \(position 21, ...ation\)'
        with self.assertRaisesRegexp(errors.ParsingError, message):
            list(options.tokenize(s))
