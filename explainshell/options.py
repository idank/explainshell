import re, collections, logging
import shlex

from cStringIO import StringIO
from explainshell import store, util

tokenstate = collections.namedtuple('tokenstate', 'startpos endpos token')

logger = logging.getLogger(__name__)

def tokenize(s):
    '''tokenize s, we use (the limited) shlex module for now, in the future
    this could be improved to a minimal bash parser

    another bit of information we return besides the tokens themselves is the start
    and end position of the token in the original string. this is tricky since
    shlex doesn't provide it and we have to look into its string pointer'''
    s = s.strip()
    stream = StringIO(s)

    lexer = shlex.shlex(stream, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''

    startpos = 0
    it = util.peekable(lexer)
    for t in it:
        endpos = stream.tell()

        # remember endpos, we're going to peek next which will move the underlying
        # string pointer
        tt = endpos

        # if we have another token, backup one char to not include the space
        # between args
        if it.hasnext():
            endpos -= 1

        # startpos is the previous endpos which may include a lot of spaces
        # between arguments

        # before: 'a     b'
        #           ^
        while s[startpos].isspace():
            startpos += 1
        # after:  'a     b'
        #                ^

        yielded = False
        if '=' in t:
            x, y = t.split('=', 1)
            # was it something like 'x=..'?
            if x:
                # was it 'x='?
                if not y:
                    # we don't want to lose the =, so yield it by itself and
                    # it will be marked as unknown by the matcher

                    # yield 'x' and '='
                    yield tokenstate(startpos, startpos+len(x), x)
                    yield tokenstate(startpos+len(x), startpos+len(x)+1, '=')
                else:
                    # yield 'x=..'
                    yield tokenstate(startpos, startpos+len(x), x)
                yielded = True
            if y:
                # yield '=y'
                yield tokenstate(startpos+len(x), endpos, '=' + y)
                yielded = True

        if not yielded:
            # no '=' in current token or it was literally just '='
            yield tokenstate(startpos, endpos, t)

        startpos = tt

def extract(manpage):
    '''extract options from all paragraphs that have been classified as containing
    options'''
    for i, p in enumerate(manpage.paragraphs):
        if p.is_option:
            s, l = extract_option(p.cleantext())
            if s or l:
                expectsarg = any(x.expectsarg for x in s + l)
                s = [x.flag for x in s]
                l = [x.flag for x in l]
                manpage.paragraphs[i] = store.option(p, s, l, expectsarg)
            else:
                logger.error("no options could be extracted from paragraph %r", p)

opt_regex = re.compile(r'''
    (?P<opt>--?(?:\?|\#|(?:\w+-)*\w+))  # option starts with - or -- and can have - in the middle but not at the end, also allow '-?'
    (?:
     (?:\s*(=)?\s*)           # -a=
     (?P<argoptional>[<\[])?  # -a=< or -a=[
     (?:\s*(=)?\s*)           # or maybe -a<=
     (?P<arg>
      (?(argoptional)         # if we think we have an arg (we saw [ or <)
       [^\]>]+                # either read everything until the closing ] or >
       |
       (?(2)
        [-a-zA-Z]+             # or if we didn't see [ or < but just saw =, read all letters, e.g. -a=abc
        |
        [A-Z]+                # but if we didn't have =, only allow uppercase letters, e.g. -a FOO
       )
      )
     )
     (?(argoptional)(?P<argoptionalc>[\]>])) # read closing ] or > if we have an arg
    )?                        # the whole arg thing is optional
    (?P<ending>,\s*|\s+|\Z|/|\|)''', re.X) # read any trailing whitespace or the end of the string

opt2_regex = re.compile(r'''
        (?P<opt>\w+)    # an option that doesn't start with any of the usual characters, e.g. options from 'dd' like bs=BYTES
        (?:
         (?:\s*=\s*)    # an optional arg, e.g. bs=BYTES
         (?P<arg>\w+)
        )
        (?:,\s*|\s+|\Z)''', re.X) # end with , or whitespace or the end of the string

def _flag(s, pos=0):
    '''
    >>> _flag('a=b').groupdict()
    {'opt': 'a', 'arg': 'b'}
    >>> bool(_flag('---c-d'))
    False
    >>> bool(_flag('foobar'))
    False
    '''
    m = opt2_regex.match(s, pos)
    return m

def _option(s, pos=0):
    '''
    >>> bool(_option('-'))
    False
    >>> bool(_option('--'))
    False
    >>> bool(_option('---'))
    False
    >>> bool(_option('-a-'))
    False
    >>> bool(_option('--a-'))
    False
    >>> bool(_option('--a-b-'))
    False
    >>> sorted(_option('-a').groupdict().iteritems())
    [('arg', None), ('argoptional', None), ('argoptionalc', None), ('ending', ''), ('opt', '-a')]
    >>> sorted(_option('--a').groupdict().iteritems())
    [('arg', None), ('argoptional', None), ('argoptionalc', None), ('ending', ''), ('opt', '--a')]
    >>> sorted(_option('-a<b>').groupdict().iteritems())
    [('arg', 'b'), ('argoptional', '<'), ('argoptionalc', '>'), ('ending', ''), ('opt', '-a')]
    >>> sorted(_option('-a=[foo]').groupdict().iteritems())
    [('arg', 'foo'), ('argoptional', '['), ('argoptionalc', ']'), ('ending', ''), ('opt', '-a')]
    >>> sorted(_option('-a=<foo>').groupdict().iteritems())
    [('arg', 'foo'), ('argoptional', '<'), ('argoptionalc', '>'), ('ending', ''), ('opt', '-a')]
    >>> sorted(_option('-a=<foo bar>').groupdict().iteritems())
    [('arg', 'foo bar'), ('argoptional', '<'), ('argoptionalc', '>'), ('ending', ''), ('opt', '-a')]
    >>> sorted(_option('-a=foo').groupdict().iteritems())
    [('arg', 'foo'), ('argoptional', None), ('argoptionalc', None), ('ending', ''), ('opt', '-a')]
    >>> bool(_option('-a=[foo>'))
    False
    >>> bool(_option('-a=[foo bar'))
    False
    >>> _option('-a foo').end(0)
    3
    '''
    m = opt_regex.match(s, pos)
    if m:
        if m.group('argoptional'):
            c = m.group('argoptional')
            cc = m.group('argoptionalc')
            if (c == '[' and cc == ']') or (c == '<' and cc == '>'):
                return m
            else:
                return
    return m

_eatbetweenregex = re.compile(r'\s*(?:or|,|\|)\s*')

def _eatbetween(s, pos):
    '''
    >>> _eatbetween('foo', 0)
    0
    >>> _eatbetween('a, b', 1)
    3
    >>> _eatbetween('a|b', 1)
    2
    >>> _eatbetween('a or b', 1)
    5
    '''
    m = _eatbetweenregex.match(s, pos)
    if m:
        return m.end(0)
    return pos

class extractedoption(collections.namedtuple('extractedoption', 'flag expectsarg')):
    def __eq__(self, other):
        if isinstance(other, str):
            return self.flag == other
        else:
            return super(extractedoption, self).__eq__(other)

    def __str__(self):
        return self.flag

def extract_option(txt):
    '''this is where the magic is (suppose) to happen. try and find options
    using a regex'''
    startpos = currpos = len(txt) - len(txt.lstrip())
    short, long = [], []

    m = _option(txt, currpos)

    # keep going as long as options are found
    while m:
        s = m.group('opt')
        po = extractedoption(s, m.group('arg'))
        if s.startswith('--'):
            long.append(po)
        else:
            short.append(po)
        currpos = m.end(0)
        currpos = _eatbetween(txt, currpos)
        if m.group('ending') == '|':
            m = _option(txt, currpos)
            if not m:
                startpos = currpos
                while currpos < len(txt) and not txt[currpos].isspace():
                    if txt[currpos] == '|':
                        short.append(extractedoption(txt[startpos:currpos], None))
                        startpos = currpos
                    currpos += 1
                leftover = txt[startpos:currpos]
                if leftover:
                    short.append(extractedoption(leftover, None))
        else:
            m = _option(txt, currpos)

    if currpos == startpos:
        m = _flag(txt, currpos)
        while m:
            s = m.group('opt')
            po = extractedoption(s, m.group('arg'))
            long.append(po)
            currpos = m.end(0)
            currpos = _eatbetween(txt, currpos)
            m = _flag(txt, currpos)

    return short, long
