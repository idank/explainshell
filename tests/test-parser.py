import unittest, functools

from explainshell import parser, errors

parse = functools.partial(parser.parse_command_line, convertpos=True)

def commandnode(s, *parts):
    return parser.Node(kind='command', s=s, parts=list(parts))

def wordnode(word, s):
    return parser.Node(kind='word', word=word, s=s)

def redirectnode(s, input, type, output):
    return parser.Node(kind='redirect', input=input, type=type, output=output, s=s)

def pipenode(pipe, s):
    return parser.Node(kind='pipe', pipe=pipe, s=s)

def pipelinenode(s, *parts):
    for i in range(len(parts)):
        if i % 2 == 0:
            assert parts[i].kind in ('command', 'compound'), parts[i].kind
        else:
            assert parts[i].kind == 'pipe', parts[i].kind
    return parser.Node(kind='pipeline', s=s, parts=list(parts))

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
        self.assertEquals(result, expected,
                'ASTs not equal\n\n%s\n\n!=\n\n%s' % (parser.dump(result), parser.dump(expected)))

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

        s = 'ab "cd" >&2 || ef\\"gh | ij >>> kl <mn \'|\' (o)'
        expected = ['ab', '"cd"', '>', '&', '2', '||',
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
                  redirectnode('>&1', 1, '>', ('&', 1)),
                  redirectnode('2>&1', 2, '>', ('&', 1))))

    def test_redirection(self):
        trythese = [('', '1'), ('', '3'), ('', '&1'), ('', '&3'), ('', 'file'),
                    ('1', '&2'), ('1', 'file'), ('1', '   file'), ('2', '/dev/null')]

        results = [(1, '1'), (1, '3'), (1, ('&', 1)), (1, ('&', 3)),
                   (1, 'file'), (1, ('&', 2)), (1, 'file'),
                   (1, 'file'), (2, '/dev/null')]

        for redirecttype in ('>', '>>'):
            for (src, dst), expected in zip(trythese, results):
                redirect = '%s%s%s' % (src, redirecttype, dst)
                s = 'a %s' % redirect
                node = commandnode(s,
                        wordnode('a', 'a'),
                        redirectnode(redirect, expected[0], redirecttype, expected[1]))
                self.assertEquals(parse(s), node)

    def test_pipeline(self):
        s = 'a | b'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            commandnode('a', wordnode('a', 'a')),
                            pipenode('|', '|'),
                            commandnode('b', wordnode('b', 'b'))))

        # negate doesn't work
        #s = '! a | b'
        #self.assertEquals(parse(s),
        #                  pipelinenode(commandnode(['a']), pipenode('|'), commandnode(['b'])))

    def test_list(self):
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
                                redirectnode('> /dev/null', 1, '>', '/dev/null')]),
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
                  redirects=[redirectnode('> /dev/null', 1, '>', '/dev/null')]
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
        self.assertRaisesRegexp(errors.ParsingError, "expected 'number'.*position 3", parse, s)

    def test_invalid_redirect(self):
        s = 'a >|b'
        self.assertRaisesRegexp(errors.ParsingError, "expecting filename or &.*position 3", parse, s)

        s = 'a >&b'
        self.assertRaisesRegexp(errors.ParsingError, "number expected after &.*position 4", parse, s)

    def test_shlex_error(self):
        s = "a 'b"
        self.assertRaisesRegexp(errors.ParsingError, "No closing quotation.*position 2", parse, s)

        s = "a b\\"
        self.assertRaisesRegexp(errors.ParsingError, "No escaped character.*position 2", parse, s)
