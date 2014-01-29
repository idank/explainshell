import unittest, functools

from explainshell import parser, errors

token = parser.token
parse = functools.partial(parser.parse_command_line, convertpos=True)
tokenize = parser.tokenize_command_line

def negatenode():
    return parser.Node(kind='negate', s='!')

def commandnode(s, *parts):
    return parser.Node(kind='command', s=s, parts=list(parts))

def wordnode(word, s):
    return parser.Node(kind='word', word=word, s=s)

def redirectnode(s, input, type, output):
    return parser.Node(kind='redirect', input=input, type=type, output=output, s=s)

def pipenode(pipe, s):
    return parser.Node(kind='pipe', pipe=pipe, s=s)

def pipelinenode(s, *parts):
    oldparts = parts
    if parts[0].kind == 'negate':
        parts = parts[1:]
    for i in range(len(parts)):
        if i % 2 == 0:
            assert parts[i].kind in ('command', 'compound'), parts[i].kind
        else:
            assert parts[i].kind == 'pipe', parts[i].kind
    return parser.Node(kind='pipeline', s=s, parts=list(oldparts))

def operatornode(op, s):
    return parser.Node(kind='operator', op=op, s=s)

def listnode(s, *parts):
    for i in range(len(parts)):
        if i % 2 == 0:
            assert parts[i].kind in ('command', 'pipeline', 'compound'), parts[i].kind
        else:
            assert parts[i].kind == 'operator', parts[i].kind
    return parser.Node(kind='list', parts=list(parts), s=s)

def compoundnode(group, list, s, redirects=[]):
    return parser.Node(kind='compound', s=s, group=group, list=list, redirects=redirects)

class test_parser(unittest.TestCase):
    def assertASTEquals(self, result, expected):
        msg = 'ASTs not equal\n\n%s\n\n!=\n\n%s' % (parser.dump(result), parser.dump(expected))
        self.assertEquals(result, expected, msg)

    def assertPositions(self, s, expected):
        p = parser.CommandLineParser(s)
        nexttoken = p.next_token()
        expected = iter(expected)

        while nexttoken[0]:
            tt, t, preceding, start, end = nexttoken
            expecteds = expected.next()
            self.assertEquals(s[start:end], expecteds)
            nexttoken = p.next_token()

    def test_positions(self):
        s = 'ab cd'
        expected = ['ab', 'cd']
        self.assertPositions(s, expected)

        s = 'ab "cd">&2 || ef\\"gh | ij >>> kl <mn \'|\' (o)'
        expected = ['ab', '"cd"', '>&', '2', '||',
                    'ef\\"gh', '|', 'ij', '>>', '>', 'kl',
                    '<', 'mn', "'|'", '(', 'o', ')']
        self.assertPositions(s, expected)

    def test_command(self):
        s = 'a b c'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  wordnode('c', 'c')))

        s = 'a b "c"'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  wordnode('c', '"c"')))

        s = '2>/dev/null a b "c"'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  redirectnode('2>/dev/null', 2, '>', '/dev/null'),
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  wordnode('c', '"c"')))

        s = 'a b>&1 2>&1'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  redirectnode('>&1', None, '>', '&1'),
                  redirectnode('2>&1', 2, '>', '&1')))

        s = '; a'
        self.assertRaisesRegexp(errors.ParsingError, "expected word or number.*position 0", parse, s)

    def test_redirection_input_here(self):
        for redirect_kind in ('<<', '<<<'):
            s = 'a %sEOF | b' % redirect_kind
            self.assertASTEquals(parse(s),
                              pipelinenode(s,
                                commandnode('a %sEOF' % redirect_kind,
                                  wordnode('a', 'a'),
                                  redirectnode('%sEOF' % redirect_kind, None, redirect_kind, 'EOF')),
                                pipenode('|', '|'),
                                commandnode('b', wordnode('b', 'b'))))

        s = 'a <<-b'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('<<-b', None, '<<', '-b')))

        s = 'a <<<<b'
        self.assertRaisesRegexp(errors.ParsingError, "expecting word after <<<.*position 5", parse, s)

    def test_redirection_input(self):
        s = 'a <f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('<f', None, '<', 'f')))

        s = 'a <1'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('<1', None, '<', '1')))

        s = 'a 1<f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('1<f', 1, '<', 'f')))

        s = 'a 1 <f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('1', '1'),
                  redirectnode('<f', None, '<', 'f')))

        s = 'a b<f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  redirectnode('<f', None, '<', 'f')))

        s = 'a 0<&3'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('0<&3', 0, '<', '&3')))

    def test_redirections1(self):
        trythese = [('', '1'), ('', '3'), ('', '&1'), ('', '&3'), ('', 'file'),
                    ('1', '&2'), ('1', 'file'), ('1', '   file'), ('2', '/dev/null')]

        results = [(None, '1'), (None, '3'), (None, '&1'), (None, '&3'),
                   (None, 'file'), (1, '&2'), (1, 'file'), (1, 'file'), (2, '/dev/null')]

        for redirecttype in ('>', '>>'):
            for (src, dst), expected in zip(trythese, results):
                redirect = '%s%s%s' % (src, redirecttype, dst)
                s = 'a %s' % redirect
                node = commandnode(s,
                        wordnode('a', 'a'),
                        redirectnode(redirect, expected[0], redirecttype, expected[1]))
                self.assertASTEquals(parse(s), node)

        s = 'a b>&f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('b', 'b'),
                  redirectnode('>&f', None, '>&', 'f')))

    def test_redirection_edges(self):
        for redirect_kind in ('>&', '<&'):
            s = 'a %s&' % redirect_kind
            self.assertRaisesRegexp(errors.ParsingError, "%s cannot redirect to fd.*position 4" % redirect_kind, parse, s)

        for redirect_kind in ('>', '>>', '>&'):
            s = 'a %s<' % redirect_kind
            self.assertRaisesRegexp(errors.ParsingError, "expecting filename or fd", parse, s)

        s = 'a 2>&foo'
        self.assertRaisesRegexp(errors.ParsingError, "fd cannot precede >& redirection.*position 2", parse, s)

        s = 'a >>&b'
        self.assertRaisesRegexp(errors.ParsingError, "fd expected after &.*position 5", parse, s)

    def test_redirections2(self):
        s = 'a &>f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('&>f', None, '&>', 'f')))

        s = 'a &>>f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('&>>f', None, '&>>', 'f')))

        s = 'a 2&>f'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  wordnode('2', '2'),
                  redirectnode('&>f', None, '&>', 'f')))

        s = 'a &> f b'
        self.assertASTEquals(parse(s),
                commandnode(s,
                  wordnode('a', 'a'),
                  redirectnode('&> f', None, '&>', 'f'),
                  wordnode('b', 'b')))

        s = 'a &>&2'
        self.assertRaisesRegexp(errors.ParsingError, "expecting filename after &>.*position 4", parse, s)

    def test_pipeline(self):
        s = 'a | b'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            commandnode('a', wordnode('a', 'a')),
                            pipenode('|', '|'),
                            commandnode('b', wordnode('b', 'b'))))

        s = '! a | b'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            negatenode(),
                            commandnode('a', wordnode('a', 'a')),
                            pipenode('|', '|'),
                            commandnode('b', wordnode('b', 'b'))
                          ))

    def test_list(self):
        s = 'a;'
        self.assertASTEquals(parse(s),
                          listnode(s,
                            commandnode('a', wordnode('a', 'a')),
                            operatornode(';', ';'),
                          ))

        s = 'a && b'
        self.assertASTEquals(parse(s),
                          listnode(s,
                            commandnode('a', wordnode('a', 'a')),
                            operatornode('&&', '&&'),
                            commandnode('b', wordnode('b', 'b'))
                          ))

        s = 'a; b; c& d'
        self.assertASTEquals(parse(s),
                          listnode(s,
                            commandnode('a', wordnode('a', 'a')),
                            operatornode(';', ';'),
                            commandnode('b', wordnode('b', 'b')),
                            operatornode(';', ';'),
                            commandnode('c', wordnode('c', 'c')),
                            operatornode('&', '&'),
                            commandnode('d', wordnode('d', 'd'))
                          ))

        s = 'a | b && c'
        self.assertASTEquals(parse(s),
                          listnode(s,
                            pipelinenode('a | b',
                              commandnode('a', wordnode('a', 'a')),
                              pipenode('|', '|'),
                              commandnode('b', wordnode('b', 'b'))),
                            operatornode('&&', '&&'),
                            commandnode('c', wordnode('c', 'c'))
                          ))

    def test_invalid_group(self):
        # unexpected reserved word
        self.assertRaisesRegexp(errors.ParsingError, "unexpected reserved word '}'.*position 0",
                                parse, '}')

        # unexpected reserved word
        self.assertRaisesRegexp(errors.ParsingError, "unexpected reserved word '}'.*position 4",
                                parse, '{a; }')

        # no terminating semicolon
        self.assertRaisesRegexp(errors.ParsingError, "group command must terminate with a semicolon.*position 4",
                                parse, '{ a}')
        self.assertRaisesRegexp(errors.ParsingError, "group command must terminate with a semicolon.*position 10",
                                parse, '{ a      }')

        # no closing }
        self.assertRaisesRegexp(errors.ParsingError, "group command must terminate with }.*position 4",
                                parse, '{ a;')

    def test_group(self):
        # reserved words are recognized only at the start of a simple command
        s = 'echo {}'
        self.assertASTEquals(parse(s),
                          commandnode(s,
                            wordnode('echo', 'echo'), wordnode('{}', '{}'))
                          )

        # reserved word at beginning isn't reserved if quoted
        s = "'{' foo"
        self.assertASTEquals(parse(s),
                          commandnode(s,
                            wordnode('{', "'{'"), wordnode('foo', 'foo'))
                          )

        s = '{ a; }'
        self.assertASTEquals(parse(s),
                          compoundnode('{',
                            listnode('a;',
                              commandnode('a', wordnode('a', 'a')),
                              operatornode(';', ';'),
                            ),
                            '{ a; }'
                          ))

        s = '{ a; b; }'
        self.assertASTEquals(parse(s),
                          compoundnode('{',
                            listnode('a; b;',
                              commandnode('a', wordnode('a', 'a')),
                              operatornode(';', ';'),
                              commandnode('b', wordnode('b', 'b')),
                              operatornode(';', ';')
                            ),
                            '{ a; b; }'
                          ))

        s = '(a) && { b; }'
        self.assertASTEquals(parse(s),
                          listnode('(a) && { b; }',
                            compoundnode('(',
                              commandnode('a',
                                wordnode('a', 'a')), '(a)'),
                            operatornode('&&', '&&'),
                            compoundnode('{',
                              listnode('b;',
                                commandnode('b',
                                  wordnode('b', 'b')),
                                operatornode(';', ';')), '{ b; }'
                              )
                          ))

        s = 'a; ! { b; }'
        self.assertASTEquals(parse(s),
                          listnode(s,
                            commandnode('a', wordnode('a', 'a')),
                            operatornode(';', ';'),
                              pipelinenode('! { b; }',
                                negatenode(),
                                compoundnode('{',
                                  listnode('b;',
                                    commandnode('b', wordnode('b', 'b')),
                                    operatornode(';', ';'),
                                  ),
                                  '{ b; }'
                                )
                              )
                          ))

    def test_compound(self):
        s = '(a) && (b)'
        self.assertASTEquals(parse(s),
                          listnode('(a) && (b)',
                            compoundnode('(',
                              commandnode('a',
                                wordnode('a', 'a')), '(a)'),
                            operatornode('&&', '&&'),
                            compoundnode('(',
                              commandnode('b',
                                wordnode('b', 'b')), '(b)'),
                          ))

        s = '(a) | (b)'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            compoundnode('(',
                              commandnode('a',
                                wordnode('a', 'a')), '(a)'),
                            pipenode('|', '|'),
                            compoundnode('(',
                              commandnode('b',
                                wordnode('b', 'b')), '(b)'),
                          ))

        s = '(a) | (b) > /dev/null'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            compoundnode('(',
                              commandnode('a',
                                wordnode('a', 'a')), '(a)'),
                            pipenode('|', '|'),
                            compoundnode('(',
                              commandnode('b',
                                wordnode('b', 'b')),
                              '(b) > /dev/null',
                              redirects=[
                                redirectnode('> /dev/null', None, '>', '/dev/null')]),
                          ))

        s = '(a && (b; c&)) || d'
        self.assertASTEquals(parse(s),
                listnode(s,
                  compoundnode('(',
                    listnode('a && (b; c&)',
                      commandnode('a',
                        wordnode('a', 'a')),
                      operatornode('&&', '&&'),
                      compoundnode('(',
                        listnode('b; c&',
                          commandnode('b',
                            wordnode('b', 'b')),
                          operatornode(';', ';'),
                          commandnode('c',
                            wordnode('c', 'c')),
                          operatornode('&', '&'),
                        ), '(b; c&)'),
                    ), '(a && (b; c&))'
                  ),
                  operatornode('||', '||'),
                  commandnode('d',
                    wordnode('d', 'd')),
                ))

    def test_compound_redirection(self):
        s = '(a) > /dev/null'
        self.assertASTEquals(parse(s),
                compoundnode('(',
                  commandnode('a',
                    wordnode('a', 'a')),
                  s,
                  redirects=[redirectnode('> /dev/null', None, '>', '/dev/null')]
                ))

    def test_compound_pipe(self):
        s = '(a) | b'
        self.assertASTEquals(parse(s),
                pipelinenode(s,
                  compoundnode('(',
                    commandnode('a',
                      wordnode('a', 'a')), '(a)'
                  ),
                  pipenode('|', '|'),
                  commandnode('b',
                    wordnode('b', 'b'))
                ))

    def test_invalid_control(self):
        s = 'a &| b'
        self.assertRaisesRegexp(errors.ParsingError, "expected word or number.*position 3", parse, s)

    def test_invalid_redirect(self):
        s = 'a >|b'
        self.assertRaisesRegexp(errors.ParsingError, "expecting filename or fd.*position 3", parse, s)

        s = 'a 2>'
        self.assertRaisesRegexp(errors.ParsingError, "expecting filename or fd.*position 4", parse, s)

    def test_shlex_error(self):
        s = "a 'b"
        self.assertRaisesRegexp(errors.ParsingError, "No closing quotation.*position 2", parse, s)

        s = "a b\\"
        self.assertRaisesRegexp(errors.ParsingError, "No escaped character.*position 2", parse, s)

    def test_tokenize(self):
        s = 'bar -x'
        t = tokenize(s)
        expected = [token('word', 'bar', '', 0, 3), token('word', '-x', ' ', 4, 6)]
        self.assertTokensEquals(s, t, expected, ('bar', '-x'))

        s = 'wx    y =z '
        t = tokenize(s)
        expected = [token('word', 'wx', '', 0, 2),
                    token('word', 'y', ' ', 6, 7), token('word', '=z', ' ', 8, 10)]
        self.assertTokensEquals(s, t, expected, ('wx', 'y', '=z'))

        s = "a 'b' c"
        t = tokenize(s)
        expected = [token('word', 'a', '', 0, 1), token('word', 'b', ' ', 2, 5),
                    token('word', 'c', ' ', 6, 7)]
        self.assertTokensEquals(s, t, expected, ('a', "'b'", 'c'))

        s = "a 'b  ' c"
        t = tokenize(s)
        expected = [token('word', 'a', '', 0, 1), token('word', 'b  ', ' ', 2, 7),
                    token('word', 'c', ' ', 8, 9)]
        self.assertTokensEquals(s, t, expected, ('a', "'b  '", 'c'))

    def test_tokenize_unknown_char(self):
        s = "a \x00bcd"
        self.assertRaisesRegexp(errors.ParsingError, "Illegal character.*position 2", parse, s)

    def test_quote_state(self):
        s = "x \"\\'\"y z"
        t = tokenize(s)
        expected = [token('word', 'x', '', 0, 1), token('word', "\\'y", ' ', 2, 7),
                    token('word', 'z', ' ', 8, 9)]
        self.assertTokensEquals(s, t, expected, ('x', "\"\\'\"y", 'z'))

    def assertTokensEquals(self, s, got, expected, substrings):
        self.assertEquals(got, expected)
        for (tt, t, preceding, start, end), ss in zip(got, substrings):
            self.assertEquals(s[start:end], ss)
