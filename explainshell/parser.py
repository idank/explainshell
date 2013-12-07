# -*- coding: utf-8 -*-
#
# Copyright (C) 2012-2013 Vinay M. Sajip. See LICENSE for licensing information.
#
import logging
import os
import collections

import sys
from explainshell import errors

from explainshell.shlext import shell_shlex

logger = logging.getLogger(__name__)

# We use a separate logger for parsing, as that's sometimes too much
# information :-)
parse_logger = logging.getLogger('%s.parse' % __name__)
#
# This runs on Python 2.x and 3.x from the same code base - no need for 2to3.
#
if sys.version_info[0] < 3:
    PY3 = False
    text_type = unicode
    binary_type = str
    string_types = basestring,
else:
    PY3 = True
    text_type = str
    binary_type = bytes
    string_types = str,
    basestring = str

class ReservedWordError(errors.ParsingError):
    def __init__(self, node, message, s, position):
        self.node = node
        super(ReservedWordError, self).__init__(message, s, position)

class Node(object):
    """
    This class represents a node in the AST built while parsing command lines.
    It's basically an object container for various attributes, with a slightly
    specialised representation to make it a little easier to debug the parser.
    """

    def __init__(self, **kwargs):
        assert 'kind' in kwargs
        self.__dict__.update(kwargs)

    def __repr__(self):
        chunks = []
        d = dict(self.__dict__)
        kind = d.pop('kind')
        for k, v in sorted(d.items()):
            chunks.append('%s=%r' % (k, v))
        return '%sNode(%s)' % (kind.title(), ' '.join(chunks))

    def __eq__(self, other):
        if not isinstance(other, Node):
            return False
        return self.__dict__ == other.__dict__

class NodeVisitor(object):
    def _visitnode(self, node, *args, **kwargs):
        k = node.kind
        self.visitnode(node)
        return getattr(self, 'visit%s' % k)(node, *args, **kwargs)

    def visit(self, node):
        k = node.kind
        if k == 'operator':
            self._visitnode(node, node.op)
        elif k == 'list':
            self._visitnode(node, node.parts)
            for child in node.parts:
                self.visit(child)
        elif k == 'negate':
            self._visitnode(node)
        elif k == 'pipe':
            self._visitnode(node, node.pipe)
        elif k == 'pipeline':
            self._visitnode(node, node.parts)
            for child in node.parts:
                self.visit(child)
        elif k == 'compound':
            self._visitnode(node, node.group, node.list, node.redirects)
            self.visit(node.list)
            for child in node.redirects:
                self.visit(child)
        elif k == 'command':
            r = self._visitnode(node, node.parts)
            for child in node.parts:
                self.visit(child)
            self.visitcommandend(node, node.parts, r)
        elif k == 'redirect':
            self._visitnode(node, node.input, node.type, node.output)
        elif k == 'word':
            self._visitnode(node, node.word)
        else:
            raise ValueError('unknown node kind %r' % k)
    def visitnode(self, node):
        pass
    def visitoperator(self, node, op):
        pass
    def visitlist(self, node, parts):
        pass
    def visitnegate(self, node):
        pass
    def visitpipe(self, node, pipe):
        pass
    def visitpipeline(self, node, parts):
        pass
    def visitcompound(self, node, group, list, redirects):
        pass
    def visitcommand(self, node, parts):
        pass
    def visitcommandend(self, node, parts, r):
        # r is the return value of the corresponding call to visitcommand
        pass
    def visitword(self, node, word):
        pass
    def visitredirect(self, node, input, type, output):
        pass

token = collections.namedtuple('token', 'type t preceding start end')

class CommandLineParser(object):
    """
    This class implements a fairly unsophisticated recursive descent parser for
    shell command lines as used in sh, bash and dash.
    """

    permitted_tokens = sorted(['<', '&&', '||', '|&', '<<', '>>', '&>', '>&', '&>>', '<<<'],
                              key=lambda s: len(s), reverse=True)
    reserved_words = ('{', '}', '!')

    def __init__(self, source, posix=None):
        self.source = source
        parse_logger.debug('starting parse of %r', source)
        if posix is None:
            posix = os.name == 'posix'
        self.lex = shell_shlex(source, posix=posix, control=True)
        self.lexpos = 0
        self.token = None
        self.peek = None

    def next_token(self):
        try:
            t = self.lex.get_token()
        except ValueError, e:
            raise errors.ParsingError(str(e), self.source, self.lexpos)
        endpos = self.lexpos
        if not t:
            tt = None
        else:
            advance = True
            tt = self.lex.token_type
            if tt in ('"', "'"):
                endpos += len(t)
                tt = 'word'
                t = t[1:-1]
            elif tt == 'a':
                endpos += len(t)
                if endpos < len(self.source) and self.source[self.lexpos] in self.lex.quotes:
                    endpos += 2
                try:
                    int(t)
                    tt = 'number'
                except ValueError:
                    tt = 'word'
            elif tt == 'c':
                advance = False
                # the shlex parser will return arbitrary runs of 'control'
                # characters, but only some runs will be valid for us. We
                # split into the valid runs and push all the others back,
                # keeping just one. Since all will have a token_type of
                # 'c', we don't need to worry about pushing the type back.
                if len(t) > 1:
                    valid = self.get_valid_controls(t)
                    t = valid.pop(0)
                    if valid:
                        for other in reversed(valid):
                            self.lex.push_token(other)
                tt = t
                endpos += len(t)
            if advance and (not self.lex.pbchars or self.lex.pbchars[0] not in self.lex.control):
                while (endpos < len(self.source) and
                       self.source[endpos] not in self.lex.whitespace):
                    endpos += 1
        assert endpos <= len(self.source), 'endpos passed the end of source (%d > %d)' % (endpos, len(self.source))
        r = token(tt, t, self.lex.preceding, self.lexpos, endpos)
        while (endpos < len(self.source) and
               self.source[endpos] in self.lex.whitespace):
            endpos += 1
        self.lexpos = endpos
        return r

    def is_reserved(self, t):
        '''limited support for reserved words'''
        if t.type != 'word':
            return False

        # must be unquoted
        if self.source[t.start] in self.lex.quotes:
            return False

        if t.t in self.reserved_words:
            return t.t

    def get_valid_controls(self, t):
        if len(t) == 1:
            result = [t]
        else:
            result = []
            while t:
                chopped = False

                # permitted_tokens is sorted by length
                for permitted in self.permitted_tokens:
                    # try to chop off permitted from the start of t
                    if t.startswith(permitted):
                        result.append(permitted)
                        t = t[len(permitted):]
                        chopped = True
                        break

                # if t isn't empty and we haven't chopped anything, add the first
                # char from t
                if t and not chopped:
                    result.append(t[0])
                    t = t[1:]
        return result

    def peek_token(self):
        if self.peek is None:
            self.peek = self.next_token()
        return self.peek.type

    def consume(self, tt):
        self.token = self.peek
        self.peek = self.next_token()
        if self.token.type != tt:
            got = self.token.type
            if got is None:
                got = 'EOF'
            raise errors.ParsingError('consume: expected %r (got %s)' % (tt, got),
                                      self.source, self.token.start)

    def parse(self):
        self.peek_token()
        return self.parse_list()

    def parse_list(self):
        parts = [self.parse_pipeline()]
        op = self.peek_token()
        while op in (';', '&', '&&', '||'):
            self.consume(op)
            tt = self.peek_token()
            s, e = self.token.start, self.token.end
            parts.append(Node(kind='operator', op=op, pos=(s, e)))
            if tt == ')' or self.is_reserved(self.peek) == '}' or (tt is None and op in (';', '&')):
                break
            part = self.parse_pipeline()
            parts.append(part)
            op = self.peek_token()
        if len(parts) == 1:
            node = parts[0]
        else:
            node = Node(kind='list', parts=parts, pos=(parts[0].pos[0], parts[-1].pos[1]))

            if self.is_reserved(self.peek):
                raise ReservedWordError(node, 'syntax: unexpected reserved word %r' % self.peek.t,
                                        self.source, self.peek.start)
            parse_logger.debug('returning %r', node)
        return node

    def parse_pipeline(self):
        tt = self.peek_token()
        parts = []
        if self.is_reserved(self.peek) == '!':
            self.consume(tt)
            parts.append(Node(kind='negate', pos=(self.token.start, self.token.end)))
        parts.append(self.parse_command())
        tt = self.peek_token()
        while tt in ('|', '|&'):
            self.consume(tt)
            s, e = self.token.start, self.token.end
            part = self.parse_command()
            parts.append(Node(kind='pipe', pipe=tt, pos=(s, e)))
            parts.append(part)
            tt = self.peek_token()
        if len(parts) == 1:
            node = parts[0]
        else:
            node = Node(kind='pipeline', parts=parts, pos=(parts[0].pos[0], parts[-1].pos[1]))
            parse_logger.debug('returning %r', node)
        return node

    def parse_reserved_word(self):
        rw = self.peek.t
        if rw == '{':
            self.consume('word')
            try:
                node = self.parse_list()
            except ReservedWordError, e:
                node = e.node
            if (node.kind != 'list' or node.parts[-1].kind != 'operator' or
                node.parts[-1].op != ';'):
                    raise errors.ParsingError('syntax: group command must '
                                              'terminate with a semicolon',
                                              self.source, node.pos[1])

            self.peek_token()
            if self.is_reserved(self.peek) != '}':
                raise errors.ParsingError('syntax: group command must terminate '
                                          'with }', self.source, self.peek.start)
            self.consume('word')
            return '{', node
        else:
            raise ReservedWordError(None, 'syntax: unexpected reserved word %r' % self.peek.t,
                                    self.source, self.peek.start)
    def parse_command(self):
        tt = self.peek_token()
        s = self.peek.start
        if tt == '(':
            self.consume(tt)
            node = self.parse_list()
            self.consume(')')
        else:
            rw = self.is_reserved(self.peek)
            if rw:
                tt, node = self.parse_reserved_word()
            else:
                return self.parse_simple_command()

        node = Node(kind='compound', group=tt, list=node, redirects=[], pos=(s, self.token.end))
        redirects, _ = self.parse_redirections(node)
        node.redirects.extend(redirects)
        if node.redirects:
            node.pos = (node.pos[0], node.redirects[-1].pos[1])
        parse_logger.debug('returning %r', node)
        return node

    def parse_simple_command(self):
        parts = self.parse_command_part()
        tt = self.peek_token()
        while tt in ('word', 'number'):
            parts.extend(self.parse_command_part())
            tt = self.peek_token()
        node = Node(kind='command', parts=parts, pos=(parts[0].pos[0], parts[-1].pos[1]))
        parse_logger.debug('returning %r', node)
        return node

    def parse_redirections1(self, node):
        # handle >, >>, >&
        tt = self.peek_token()
        input = None
        start = self.peek.start
        if self.peek.preceding == '':
            assert node.kind == 'word'
            # > or >> or >& seen without preceding whitespace. So see if the
            # last token is a positive integer. If it is, assume it's
            # an fd to redirect and pop it, else leave it in as part of
            # the command line.
            try:
                try_num = int(node.word)
                if try_num > 0:
                    input = try_num
            except ValueError:
                pass
        redirect_kind = tt
        self.consume(tt)
        tt = self.peek_token()

        # >& followed with &
        if tt == '&' and redirect_kind == '>&':
            raise errors.ParsingError('syntax: >& cannot redirect to fd', self.source, self.peek.start)

        # need word/number/& after >/>>/>&
        if tt not in ('word', 'number', '&'):
            raise errors.ParsingError('syntax: expecting filename or fd', self.source, self.peek.start)

        # don't accept 2>&filename
        if redirect_kind == '>&' and tt != 'number' and input is not None:
            raise errors.ParsingError('syntax: fd cannot precede >& redirection',
                                      self.source, node.pos[0])

        output = ''
        if tt == '&':
            # >>&
            self.consume('&')
            tt = self.peek_token()
            if tt != 'number':
                raise errors.ParsingError('syntax: fd expected after &', self.source, self.peek.start)
            output += '&'
        elif redirect_kind == '>&' and tt == 'number':
            # >&n, change redirect kind to '>'
            redirect_kind = '>'
            output += '&'

        output += self.peek.t
        self.consume(tt)

        if input is not None:
            start = node.pos[0]
            node = None

        redirect = Node(kind='redirect', input=input, type=redirect_kind,
                        output=output, pos=(start, self.token.end))
        return redirect, node

    def parse_redirections2(self):
        # handle &>, &>>
        tt = self.peek_token()
        assert tt in ('&>', '&>>')
        redirect_kind = tt
        self.consume(tt)
        tt = self.peek_token()
        if tt not in ('word', 'number'):
            raise errors.ParsingError('syntax: expecting filename after %s' % redirect_kind,
                                      self.source, self.peek.start)
        start = self.token.start
        redirect_target = self.peek.t
        self.consume(tt)
        redirect = Node(kind='redirect', input=None, type=redirect_kind,
                        output=redirect_target, pos=(start, self.token.end))
        return redirect

    def parse_redirection_input(self, node):
        tt = self.peek_token()
        assert tt == '<'
        input = None
        start = self.peek.start
        if self.peek.preceding == '':
            assert node.kind == 'word'
            # < seen without preceding whitespace. So see if the
            # last token is a positive integer. If it is, assume it's
            # an fd to redirect and pop it, else leave it in as part of
            # the command line.
            try:
                try_num = int(node.word)
                if try_num > 0:
                    input = try_num
            except ValueError:
                pass
        redirect_kind = tt
        self.consume(tt)
        tt = self.peek_token()
        if tt not in ('word', 'number'):
            raise errors.ParsingError('syntax: expecting filename after <',
                                      self.source, self.peek.start)
        self.consume(tt)

        if input is not None:
            start = node.pos[0]
            node = None

        redirect = Node(kind='redirect', input=input, type=redirect_kind,
                        output=output, pos=(start, self.token.end))
        return redirect, node

    def parse_redirection_input_here(self):
        # handle <<, <<<
        tt = self.peek_token()
        assert tt in ('<<', '<<<')
        redirect_kind = tt
        self.consume(tt)
        tt = self.peek_token()
        if tt not in ('word', 'number'):
            raise errors.ParsingError('syntax: expecting word after %s' % redirect_kind,
                                      self.source, self.peek.start)
        start = self.token.start
        redirect_target = self.peek.t
        self.consume(tt)
        redirect = Node(kind='redirect', input=None, type=redirect_kind,
                        output=redirect_target, pos=(start, self.token.end))
        return redirect

    def parse_redirections(self, node):
        parts = []
        tt = self.peek_token()
        while tt in ('<', '>', '<<', '>>', '&>', '>&', '&>>', '<<<'):
            if tt in ('>', '>>', '>&'):
                part, node = self.parse_redirections1(node)
                parts.append(part)
            elif tt == '<':
                part, node = self.parse_redirections1(node)
                parts.append(part)
            elif tt in ('&>', '&>>'):
                parts.append(self.parse_redirections2())
            elif tt in ('<<', '<<<'):
                parts.append(self.parse_redirection_input_here())
            else:
                assert False, tt
            tt = self.peek_token()
        return parts, node is None

    def parse_command_part(self):
        node = Node(kind='word', word=self.peek.t,
                    pos=(self.peek.start, self.peek.end))
        if self.peek.type == 'word':
            self.consume('word')
        elif self.peek.type == 'number':
            self.consume('number')
        else:
            raise errors.ParsingError('syntax: expected word or number', self.source, self.peek.start)
        redirections, usednode = self.parse_redirections(node)
        if usednode:
            return redirections
        else:
            return [node] + redirections

def tokenize_command_line(source, posix=None):
    if posix is None:
        posix = os.name == 'posix'
    parser = CommandLineParser(source, posix=posix)
    tokens = []
    while True:
        token = parser.next_token()
        if token.t is None:
            break
        tokens.append(token)
    return tokens

def parse_command_line(source, posix=None, convertpos=False):
    """
    Parse a command line into an AST.

    :param source: The command line to parse.
    :type source: str
    :param posix: Whether Posix conventions are used in the lexer.
    :type posix: bool
    """
    if posix is None:
        posix = os.name == 'posix'
    cmdparser = CommandLineParser(source, posix=posix)
    ast = cmdparser.parse()
    if convertpos:
        class v(NodeVisitor):
            def visitnode(self, node):
                s, e = node.__dict__.pop('pos')
                node.s = source[s:e]
        v().visit(ast)
    logger.info('parsed %r as %r', source, ast)
    return ast

def dump(node, indent='  '):
    def _format(node, level=0):
        if isinstance(node, Node):
            d = node.__dict__
            kind = d.pop('kind')
            if kind == 'list' and level > 0:
                level = level + 1
            fields = [(k, _format(v, level)) for k, v in sorted(d.items()) if v]
            if kind == 'list' and level > 0:
                return ''.join([
                    '\n%s%sNode' % (indent * level, kind.title()),
                    '(',
                    ', '.join(('%s=%s' % field for field in fields)),
                    ')'])
            return ''.join([
                '%sNode' % kind.title(),
                '(',
                ', '.join(('%s=%s' % field for field in fields)),
                ')'])
        elif isinstance(node, list):
            lines = ['[']
            lines.extend((indent * (level + 2) + _format(x, level + 2) + ','
                         for x in node))
            if len(lines) > 1:
                lines.append(indent * (level + 1) + ']')
            else:
                lines[-1] += ']'
            return '\n'.join(lines)
        return repr(node)
    if not isinstance(node, Node):
        raise TypeError('expected Node, got %r' % node.__class__.__name__)
    return _format(node)

def findfirstkind(parts, kind):
    for i, node in enumerate(parts):
        if node.kind == kind:
            return i
    return -1
