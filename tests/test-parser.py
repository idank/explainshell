import unittest, functools

from explainshell import parser

parse = functools.partial(parser.parse_command_line, convertpos=True)

def commandnode(command, s, redirects=[]):
    return parser.Node(kind='command', s=s, command=command, redirects=redirects)

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
        self.assertASTEquals(parse(s), commandnode(['a', 'b', 'c'], 'a b c'))
        s = 'a b "c"'
        self.assertASTEquals(parse(s), commandnode(['a', 'b', 'c'], 'a b "c"'))
        s = '2>/dev/null a b "c"'
        self.assertASTEquals(parse(s),
                commandnode(['a', 'b', 'c'], s, redirects=[(2, '>', '/dev/null')]))
        s = 'a b>&1 2>&1'
        self.assertASTEquals(parse(s),
                commandnode(['a', 'b'], s, redirects=[(1, '>', ('&', 1)), (2, '>', ('&', 1))]))

    def test_redirection(self):
        trythese = [('', '1'), ('', '3'), ('', '&1'), ('', '&3'), ('', 'file'),
                    ('1', '&2'), ('1', 'file'), ('1', '   file'), ('2', '/dev/null')]

        results = [(1, '1'), (1, '3'), (1, ('&', 1)), (1, ('&', 3)),
                   (1, 'file'), (1, ('&', 2)), (1, 'file'),
                   (1, 'file'), (2, '/dev/null')]

        for redirecttype in ('>', '>>'):
            for (src, dst), expected in zip(trythese, results):
                s = 'a %s%s%s' % (src, redirecttype, dst)
                node = commandnode(['a'], s, [(expected[0], redirecttype, expected[1])])
                self.assertEquals(parse(s), node)

    def test_pipeline(self):
        s = 'a | b'
        self.assertEquals(parse(s),
                          pipelinenode(s,
                            commandnode(['a'], 'a'),
                            pipenode('|', '|'),
                            commandnode(['b'], 'b')))

        # negate doesn't work
        #s = '! a | b'
        #self.assertEquals(parse(s),
        #                  pipelinenode(commandnode(['a']), pipenode('|'), commandnode(['b'])))

    def test_list(self):
        s = 'a && b'
        self.assertEquals(parse(s),
                          listnode(s,
                            commandnode(['a'], 'a'),
                            operatornode('&&', '&&'),
                            commandnode(['b'], 'b')
                          ))

        s = 'a; b; c& d'
        self.assertEquals(parse(s),
                          listnode(s,
                            commandnode(['a'], 'a'),
                            operatornode(';', ';'),
                            commandnode(['b'], 'b'),
                            operatornode(';', ';'),
                            commandnode(['c'], 'c'),
                            operatornode('&', '&'),
                            commandnode(['d'], 'd')
                          ))

        s = 'a | b && c'
        self.assertEquals(parse(s),
                          listnode(s,
                            pipelinenode('a | b',
                              commandnode(['a'], 'a'),
                              pipenode('|', '|'),
                              commandnode(['b'], 'b')),
                            operatornode('&&', '&&'),
                            commandnode(['c'], 'c')
                          ))

    def test_compound(self):
        s = '(a) && (b)'
        self.assertASTEquals(parse(s),
                          listnode('(a) && (b)',
                            compoundnode('(', commandnode(['a'], 'a'), '(a)'),
                            operatornode('&&', '&&'),
                            compoundnode('(', commandnode(['b'], 'b'), '(b)'),
                          ))

        s = '(a) | (b)'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            compoundnode('(', commandnode(['a'], 'a'), '(a)'),
                            pipenode('|', '|'),
                            compoundnode('(', commandnode(['b'], 'b'), '(b)'),
                          ))

        s = '(a) | (b) > /dev/null'
        self.assertASTEquals(parse(s),
                          pipelinenode(s,
                            compoundnode('(', commandnode(['a'], 'a'), '(a)'),
                            pipenode('|', '|'),
                            compoundnode('(', commandnode(['b'], 'b'), '(b) > /dev/null',
                                         redirects=[(1, '>', '/dev/null')]),
                          ))

        s = '(a && (b; c&)) || d'
        self.assertASTEquals(parse(s),
                listnode(s,
                  compoundnode('(',
                    listnode('a && (b; c&)',
                      commandnode(['a'], 'a'),
                      operatornode('&&', '&&'),
                      compoundnode('(',
                        listnode('b; c&',
                          commandnode(['b'], 'b'),
                          operatornode(';', ';'),
                          commandnode(['c'], 'c'),
                          operatornode('&', '&'),
                        ), '(b; c&)'),
                    ), '(a && (b; c&))'
                  ),
                  operatornode('||', '||'),
                  commandnode(['d'], 'd'),
                ))

    def test_compound_redirection(self):
        s = '(a) > /dev/null'
        self.assertASTEquals(parse(s),
                compoundnode('(',
                  commandnode(['a'], 'a'),
                  s,
                  redirects=[(1, '>', '/dev/null')]
                ))

    def test_compound_pipe(self):
        s = '(a) | b'
        self.assertASTEquals(parse(s),
                pipelinenode(s,
                  compoundnode('(',
                    commandnode(['a'], 'a'), '(a)'
                  ),
                  pipenode('|', '|'),
                  commandnode(['b'], 'b')
                ))
