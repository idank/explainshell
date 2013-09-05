import unittest

from explainshell import parser

parse = parser.parse_command_line

def commandnode(command, redirects=[]):
    return parser.Node(kind='command', command=command, redirects=redirects)

def pipenode(pipe):
    return parser.Node(kind='pipe', pipe=pipe)

def pipelinenode(*parts):
    for i in range(len(parts)):
        if i % 2 == 0:
            assert parts[i].kind in ('command', 'compound')
        else:
            assert parts[i].kind == 'pipe'
    return parser.Node(kind='pipeline', parts=list(parts))

def operatornode(op):
    return parser.Node(kind='operator', op=op)

def listnode(*parts):
    for i in range(len(parts)):
        if i % 2 == 0:
            assert parts[i].kind in ('command', 'pipeline', 'compound')
        else:
            assert parts[i].kind == 'operator'
    return parser.Node(kind='list', parts=list(parts))

def compoundnode(group, list, redirects=[]):
    return parser.Node(kind='compound', group=group, list=list, redirects=redirects)

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

        s = 'ab "cd" >&2 || ef\\"gh | ij >>> kl <mn \'|\''
        expected = ['ab', '"cd"', '>', '&', '2', '||',
                    'ef\\"gh', '|', 'ij', '>>', '>', 'kl',
                    '<', 'mn', "'|'"]
        self.assertPositions(s, expected)

    def test_command(self):
        s = 'a b c'
        self.assertEquals(parse(s), commandnode(['a', 'b', 'c']))
        s = 'a b "c"'
        self.assertEquals(parse(s), commandnode(['a', 'b', 'c']))
        s = '2>/dev/null a b "c"'
        self.assertASTEquals(parse(s),
                commandnode(['a', 'b', 'c'], redirects=[(2, '>', '/dev/null')]))

    def test_redirection(self):
        trythese = [('', '1'), ('', '3'), ('', '&1'), ('', '&3'), ('', 'file'),
                    ('1', '&2'), ('1', 'file'), ('1', '   file'), ('2', '/dev/null')]

        results = [(1, '1'), (1, '3'), (1, ('&', 1)), (1, ('&', 3)),
                   (1, 'file'), (1, ('&', 2)), (1, 'file'),
                   (1, 'file'), (2, '/dev/null')]

        for redirecttype in ('>', '>>'):
            for (src, dst), expected in zip(trythese, results):
                s = 'a %s%s%s' % (src, redirecttype, dst)
                node = commandnode(['a'], [(expected[0], redirecttype, expected[1])])
                self.assertEquals(parse(s), node)

    def test_pipeline(self):
        s = 'a | b'
        self.assertEquals(parse(s),
                          pipelinenode(
                            commandnode(['a']),
                            pipenode('|'),
                            commandnode(['b'])))

        # negate doesn't work
        #s = '! a | b'
        #self.assertEquals(parse(s),
        #                  pipelinenode(commandnode(['a']), pipenode('|'), commandnode(['b'])))

    def test_list(self):
        s = 'a && b'
        self.assertEquals(parse(s),
                          listnode(
                            commandnode(['a']),
                            operatornode('&&'),
                            commandnode(['b'])
                          ))

        s = 'a; b; c& d'
        self.assertEquals(parse(s),
                          listnode(
                            commandnode(['a']),
                            operatornode(';'),
                            commandnode(['b']),
                            operatornode(';'),
                            commandnode(['c']),
                            operatornode('&'),
                            commandnode(['d'])
                          ))

        s = 'a | b && c'
        self.assertEquals(parse(s),
                          listnode(
                            pipelinenode(
                              commandnode(['a']),
                              pipenode('|'),
                              commandnode(['b'])),
                            operatornode('&&'),
                            commandnode(['c'])
                          ))

    def test_compound(self):
        s = '(a) && (b)'
        self.assertASTEquals(parse(s),
                          listnode(
                            compoundnode('(', commandnode(['a'])),
                            operatornode('&&'),
                            compoundnode('(', commandnode(['b'])),
                          ))

        s = '(a) | (b)'
        self.assertASTEquals(parse(s),
                          pipelinenode(
                            compoundnode('(', commandnode(['a'])),
                            pipenode('|'),
                            compoundnode('(', commandnode(['b'])),
                          ))

        s = '(a) | (b) > /dev/null'
        self.assertASTEquals(parse(s),
                          pipelinenode(
                            compoundnode('(', commandnode(['a'])),
                            pipenode('|'),
                            compoundnode('(', commandnode(['b']),
                                         redirects=[(1, '>', '/dev/null')]),
                          ))

        s = '(a && (b; c&)) || d'
        self.assertASTEquals(parse(s),
                listnode(
                  compoundnode('(',
                    listnode(
                      commandnode(['a']),
                      operatornode('&&'),
                      compoundnode('(',
                        listnode(
                          commandnode(['b']),
                          operatornode(';'),
                          commandnode(['c']),
                          operatornode('&'),
                        )),
                    )
                  ),
                  operatornode('||'),
                  commandnode(['d']),
                ))

    def test_compound_redirection(self):
        s = '(a) > /dev/null'
        self.assertASTEquals(parse(s),
                compoundnode('(',
                  commandnode(['a']),
                  redirects=[(1, '>', '/dev/null')]
                ))

    def test_compound_pipe(self):
        s = '(a) | b'
        self.assertASTEquals(parse(s),
                pipelinenode(
                  compoundnode('(',
                    commandnode(['a'])
                  ),
                  pipenode('|'),
                  commandnode(['b'])
                ))
