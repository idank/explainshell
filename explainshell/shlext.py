# -*- coding: utf-8 -*-
#
# Copyright (C) 2012-2013 Vinay M. Sajip. See LICENSE for licensing information.
#
# Enhancements in shlex to tokenize closer to the way real shells do
#
from collections import deque
import shlex
import sys

# We need to behave differently on 2,x and 3,x, because on 2.x
# shlex barfs on Unicode, and must be given str.

if sys.version_info[0] < 3:
    PY3 = False
    text_type = unicode
else:
    PY3 = True
    text_type = str

class shell_shlex(shlex.shlex):
    def __init__(self, instream=None, **kwargs):
        if 'control' not in kwargs:
            control = ''
        else:
            control = kwargs.pop('control')
            if control is True:
                control = '();<>|&'
        # shlex on 2.x doesn't like being passed Unicode :-(
        if not PY3 and isinstance(instream, text_type):
            instream = instream.encode('utf-8')
        shlex.shlex.__init__(self, instream, **kwargs)
        self.control = control
        self.wordchars += '+-./*?=$%:@~`^,[]{}!\\'   # these chars allowed in params
        if self.control:
            self.pbchars = deque()

    def read_token(self):
        quoted = False
        escapedstate = ' '
        self.preceding = ''
        while True:
            if self.control and self.pbchars:
                nextchar = self.pbchars.pop()
            else:
                nextchar = self.instream.read(1)
            if nextchar == '\n':
                self.lineno += 1
            if self.debug >= 3: # pragma: no cover
                print("shlex: in state %r saw %r" % (self.state, nextchar))
            if self.state is None:
                self.token = ''        # past end of file
                break
            elif self.state == ' ':
                if not nextchar:
                    self.token_type = self.state
                    self.state = None  # end of file
                    break
                elif nextchar in self.whitespace:
                    self.preceding = nextchar
                    if self.debug >= 2: # pragma: no cover
                        print("shlex: whitespace in whitespace state")
                    if self.token or (self.posix and quoted):
                        break   # emit current token
                    else:
                        continue
                elif nextchar in self.commenters:
                    self.instream.readline()
                    self.lineno += 1
                    self.preceding = '\n'
                elif self.posix and nextchar in self.escape:
                    escapedstate = 'a'
                    self.token_type = self.state
                    self.state = nextchar
                elif nextchar in self.wordchars:
                    self.token = nextchar
                    self.token_type = self.state
                    self.state = 'a'
                elif nextchar in self.control:
                    self.token = nextchar
                    self.token_type = self.state
                    self.state = 'c'
                elif nextchar in self.quotes:
                    if not self.posix:
                        self.token = nextchar
                    self.token_type = self.state
                    self.state = nextchar
                elif self.whitespace_split:
                    self.token = nextchar
                    self.token_type = self.state
                    self.state = 'a'
                else:
                    self.token = nextchar
                    if self.token or (self.posix and quoted):
                        break   # emit current token
                    else:
                        continue
            elif self.state in self.quotes:
                quoted = True
                if not nextchar:      # end of file
                    if self.debug >= 2: # pragma: no cover
                        print("shlex: I see EOF in quotes state")
                    # XXX what error should be raised here?
                    raise ValueError("No closing quotation")
                if nextchar == self.state:
                    self.token_type = self.state
                    if not self.posix:
                        self.token += nextchar
                        self.state = ' '
                        break
                    else:
                        self.state = 'a'
                elif (self.posix and nextchar in self.escape and self.state
                      in self.escapedquotes):
                    escapedstate = self.state
                    self.token_type = self.state
                    self.state = nextchar
                else:
                    self.token += nextchar
            elif self.state in self.escape:
                if not nextchar:      # end of file
                    if self.debug >= 2: # pragma: no cover
                        print("shlex: I see EOF in escape state")
                    # XXX what error should be raised here?
                    raise ValueError("No escaped character")
                # In posix shells, only the quote itself or the escape
                # character may be escaped within quotes.
                if (escapedstate in self.quotes and nextchar != self.state
                    and nextchar != escapedstate):
                    self.token += self.state
                self.token += nextchar
                self.token_type = self.state
                self.state = escapedstate
            elif self.state in ('a', 'c'):
                if not nextchar:
                    self.token_type = self.state
                    self.state = None   # end of file
                    break
                elif nextchar in self.whitespace:
                    if self.debug >= 2: # pragma: no cover
                        print("shlex: I see whitespace in word state")
                    self.token_type = self.state
                    self.state = ' '
                    if self.token or (self.posix and quoted):
                        # push back so that preceding is set
                        # correctly for the next token
                        if self.control:
                            self.pbchars.append(nextchar)
                        break   # emit current token
                    else:
                        continue
                elif nextchar in self.commenters:
                    self.instream.readline()
                    self.lineno += 1
                    if self.posix:
                        self.token_type = self.state
                        self.state = ' '
                        if self.token or (self.posix and quoted):
                            break   # emit current token
                        else:
                            continue
                elif self.posix and nextchar in self.quotes:
                    self.token_type = self.state
                    self.state = nextchar
                elif self.posix and nextchar in self.escape:
                    escapedstate = 'a'
                    self.token_type = self.state
                    self.state = nextchar
                elif self.state == 'c':
                    if nextchar in self.control:
                        self.token += nextchar
                    else:
                        if nextchar not in self.whitespace:
                            self.pbchars.append(nextchar)
                        else:
                            self.preceding = nextchar
                        self.token_type = self.state
                        self.state = ' '
                        break
                elif (nextchar in self.wordchars or nextchar in self.quotes
                      or self.whitespace_split):
                    self.token += nextchar
                else:
                    if self.control:
                        self.pbchars.append(nextchar)
                    else:
                        self.pushback.appendleft(nextchar)
                    if self.debug >= 2: # pragma: no cover
                        print("shlex: I see punctuation in word state")
                    self.token_type = self.state
                    self.state = ' '
                    if self.token:
                        break   # emit current token
                    else:
                        continue
        result = self.token
        self.token = ''
        if self.posix and not quoted and result == '':
            result = None
        if self.debug > 1:  # pragma: no cover
            if result:
                print("shlex: raw token=" + repr(result))
            else:
                print("shlex: raw token=EOF")
        return result
