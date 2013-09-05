# -*- coding: utf-8 -*-
#
# Copyright (C) 2012-2013 Vinay M. Sajip. See LICENSE for licensing information.
#
import logging
import os
import collections

import sys

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
        getattr(self, 'visit%s' % k)(node, *args, **kwargs)

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
        elif k == 'command':
            self._visitnode(node, node.command, node.redirects)
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
    def visitcommand(self, node, command, redirects):
        pass

token = collections.namedtuple('token', 'type t preceding start end')

class CommandLineParser(object):
    """
    This class implements a fairly unsophisticated recursive descent parser for
    shell command lines as used in sh, bash and dash.
    """

    permitted_tokens = ('&&', '||', '|&', '>>')

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
        t = self.lex.get_token()
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
                if self.source[self.lexpos] in self.lex.quotes:
                    endpos += 1
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
        r = token(tt, t, self.lex.preceding, self.lexpos, endpos)
        while (endpos < len(self.source) and
               self.source[endpos] in self.lex.whitespace):
            endpos += 1
        self.lexpos = endpos
        return r

    def get_valid_controls(self, t):
        if len(t) == 1:
            result = [t]
        else:
            result = []
            last = None
            for c in t:
                if last is not None:
                    combined = last + c
                    if combined in self.permitted_tokens:
                        result.append(combined)
                    else:
                        result.append(last)
                        result.append(c)
                    last = None
                elif c not in ('>', '&', '|'):
                    result.append(c)
                else:
                    last = c
            if last:
                result.append(last)
                #logger.debug('%s -> %s', t, result)
        return result

    def peek_token(self):
        if self.peek is None:
            self.peek = self.next_token()
        return self.peek.type

    def consume(self, tt):
        self.token = self.peek
        self.peek = self.next_token()
        if self.token.type != tt:
            raise ValueError('consume: expected %r' % tt)

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
            if tt in (')', '}'):
                parts.append(Node(kind='operator', op=op, pos=(s, e)))
                break
            part = self.parse_pipeline()
            parts.append(Node(kind='operator', op=op, pos=(s, e)))
            parts.append(part)
            op = self.peek_token()
        if len(parts) == 1:
            node = parts[0]
        else:
            node = Node(kind='list', parts=parts, pos=(parts[0].pos[0], parts[-1].pos[1]))
            parse_logger.debug('returning %r', node)
        return node

    def parse_pipeline(self):
        tt = self.peek_token()
        parts = []
        if tt == '!':
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

    def parse_command(self):
        tt = self.peek_token()
        if tt == '(':
            self.consume(tt)
            s = self.token.start
            node = self.parse_list()
            self.consume(')')
        elif tt == '{':
            self.consume(tt)
            s = self.token.start
            node = self.parse_list()
            self.consume('}')
        else:
            return self.parse_simple_command()

        node = Node(kind='compound', group=tt, list=node, redirects=[], pos=(s, self.token.end))
        self.parse_redirections(node)
        parse_logger.debug('returning %r', node)
        return node

    def parse_simple_command(self):
        node = self.parse_command_part()
        tt = self.peek_token()
        while tt in ('word', 'number'):
            part = self.parse_command_part()
            node.command.extend(part.command)
            node.redirects.extend(part.redirects)
            tt = self.peek_token()
        node.pos = (node.pos[0], self.token.end)
        parse_logger.debug('returning %r', node)
        return node

    def parse_redirections(self, node):
        tt = self.peek_token()
        while tt in ('>', '>>'):
            num = 1     # default value
            if self.peek.preceding == '':
                # > or >> seen without preceding whitespace. So see if the
                # last token is a positive integer. If it is, assume it's
                # an fd to redirect and pop it, else leave it in as part of
                # the command line.
                try:
                    try_num = int(node.command[-1])
                    if try_num > 0:
                        num = try_num
                        node.command.pop()
                except ValueError:
                    pass
            redirect_kind = tt
            self.consume(tt)
            tt = self.peek_token()
            if tt not in ('word', 'number', '&'):
                raise ValueError('syntax: expecting filename or &')
            if tt in ('word', 'number'):
                redirect_target = self.peek.t
                self.consume(tt)
            else:
                self.consume('&')
                if self.peek_token() != 'number':
                    raise ValueError('syntax: number expected after &')
                n = int(self.peek.t)
                redirect_target = ('&', n)
                self.consume('number')
            node.redirects.append((num, redirect_kind, redirect_target))
            tt = self.peek_token()
        node.pos = (node.pos[0], self.token.end)
        return node

    def parse_command_part(self):
        node = Node(kind='command', command=[self.peek.t], redirects=[],
                    pos=(self.peek.start, self.peek.end))
        if self.peek.type == 'word':
            self.consume('word')
        else:
            self.consume('number')
        self.parse_redirections(node)
        return node

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
    ast = CommandLineParser(source, posix=posix).parse()
    if convertpos:
        class v(NodeVisitor):
            def visitnode(self, node):
                s, e = node.__dict__.pop('pos')
                node.s = source[s:e]
        v().visit(ast)
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
