import collections
import logging
import re

from explainshell import store

token_state = collections.namedtuple("token_state", "startpos endpos token")

logger = logging.getLogger(__name__)


def extract(manpage):
    """extract options from all paragraphs that have been classified as containing
    options"""
    for i, p in enumerate(manpage.paragraphs):
        if p.is_option:
            s, ln = extract_option(p.cleantext())
            if s or ln:
                expects_arg = any(x.expects_arg for x in s + ln)
                s = [x.flag for x in s]
                ln = [x.flag for x in ln]
                manpage.paragraphs[i] = store.Option(p, s, ln, expects_arg)
            else:
                logger.error("no options could be extracted from paragraph %r", p)


opt_regex = re.compile(
    r"""
    (?P<opt>--?(?:\?|\#|(?:\w+-)*\w+))  # option starts with - or -- and can have - in the middle but not at the end, also allow '-?'
    (?:
     (?:\s?(=)?\s?)           # -a=
     (?P<argoptional>[<\[])?  # -a=< or -a=[
     (?:\s?(=)?\s?)           # or maybe -a<=
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
    (?P<ending>,\s*|\s+|\Z|/|\|)""",
    re.X,
)  # read any trailing whitespace or the end of the string

opt2_regex = re.compile(
    r"""
        (?P<opt>\w+)    # an option that doesn't start with any of the usual characters, e.g. options from 'dd' like bs=BYTES
        (?:
         (?:\s*=\s*)    # an optional arg, e.g. bs=BYTES
         (?P<arg>\w+)
        )
        (?:,\s*|\s+|\Z)""",
    re.X,
)  # end with , or whitespace or the end of the string


def _flag(s, pos=0):
    """
    >>> _flag('a=b').groupdict()
    {'opt': 'a', 'arg': 'b'}
    >>> bool(_flag('---c-d'))
    False
    >>> bool(_flag('foobar'))
    False
    """
    m = opt2_regex.match(s, pos)
    return m


def _option(s, pos=0):
    """
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
    """
    m = opt_regex.match(s, pos)
    if m:
        if m.group("argoptional"):
            c = m.group("argoptional")
            cc = m.group("argoptionalc")
            if (c == "[" and cc == "]") or (c == "<" and cc == ">"):
                return m
            else:
                return
    return m


_eat_between_regex = re.compile(r"\s*(?:or|,|\|)\s*")


def _eat_between(s, pos):
    """
    >>> _eat_between('foo', 0)
    0
    >>> _eat_between('a, b', 1)
    3
    >>> _eat_between('a|b', 1)
    2
    >>> _eat_between('a or b', 1)
    5
    """
    m = _eat_between_regex.match(s, pos)
    if m:
        return m.end(0)
    return pos


class ExtractedOption(collections.namedtuple("ExtractedOption", "flag expects_arg")):
    def __eq__(self, other):
        if isinstance(other, str):
            return self.flag == other
        else:
            return super().__eq__(other)

    def __str__(self):
        return self.flag


def extract_option(txt):
    """this is where the magic is (suppose) to happen. try and find options
    using a regex"""
    start_pos = curr_pos = len(txt) - len(txt.lstrip())
    short, long = [], []

    m = _option(txt, curr_pos)

    # keep going as long as options are found
    while m:
        s = m.group("opt")
        po = ExtractedOption(s, m.group("arg"))
        if s.startswith("--"):
            long.append(po)
        else:
            short.append(po)
        curr_pos = m.end(0)
        curr_pos = _eat_between(txt, curr_pos)
        if m.group("ending") == "|":
            m = _option(txt, curr_pos)
            if not m:
                start_pos = curr_pos
                while curr_pos < len(txt) and not txt[curr_pos].isspace():
                    if txt[curr_pos] == "|":
                        short.append(ExtractedOption(txt[start_pos:curr_pos], None))
                        start_pos = curr_pos
                    curr_pos += 1
                leftover = txt[start_pos:curr_pos]
                if leftover:
                    short.append(ExtractedOption(leftover, None))
        else:
            m = _option(txt, curr_pos)

    if curr_pos == start_pos:
        m = _flag(txt, curr_pos)
        while m:
            s = m.group("opt")
            po = ExtractedOption(s, m.group("arg"))
            long.append(po)
            curr_pos = m.end(0)
            curr_pos = _eat_between(txt, curr_pos)
            m = _flag(txt, curr_pos)

    return short, long
