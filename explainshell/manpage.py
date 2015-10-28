import os, subprocess, re, logging, collections, urllib

from explainshell import config, store, errors

devnull = open(os.devnull, 'w')
SPLITSYNOP = re.compile(r'([^ ]+) - (.*)$')

ENV = dict(os.environ)
ENV["W3MMAN_MAN"] = "man --no-hyphenation"
ENV["MAN_KEEP_FORMATTING"] = "1"
ENV["MANWIDTH"] = "115"
ENV["LC_ALL"] = "en_US.UTF-8"

logger = logging.getLogger(__name__)

def extractname(gzname):
    '''
    >>> extractname('ab.1.gz')
    'ab'
    >>> extractname('ab.1.1.gz')
    'ab.1'
    >>> extractname('ab.1xyz.gz')
    'ab'
    >>> extractname('ab.1.1xyz.gz')
    'ab.1'
    >>> extractname('a/b/c/ab.1.1xyz.gz')
    'ab.1'
    '''
    if '/' in gzname:
        gzname = os.path.basename(gzname)
    return gzname.rsplit('.', 2)[0]

def bold(l):
    '''
    >>> bold('a')
    ([], ['a'])
    >>> bold('<b>a</b>')
    (['a'], [])
    >>> bold('a<b>b</b>c')
    (['b'], ['a', 'c'])
    >>> bold('<b>first</b> <b>second:</b>')
    (['first', 'second:'], [])
    '''
    inside = []
    for m in _section.finditer(l):
        inside.append(m.span(0))

    current = 0
    outside = []
    for start, end in inside:
        outside.append((current, start))
        current = end
    outside.append((current, len(l)))

    inside = [l[s:e] for s, e in inside]
    inside = [s.replace('<b>', '').replace('</b>', '') for s in inside]

    outside = [l[s:e] for s, e in outside]
    outside = [l for l in outside if l and not l.isspace()]
    return inside, outside

# w3mman2html.cgi (the tool we're using to output html from a man page) does
# some strange escaping which causes it to output invalid utf8. we look these
# up and fix them manually
_replacementsprefix = [
        ('\xe2\x80\xe2\x80\x98', None, True), # left single quote
        ('\xe2\x80\xe2\x80\x99', None, True), # right single quote
        ('\xe2\x80\xe2\x80\x9c', None, True), # left double quote
        ('\xe2\x80\xe2\x80\x9d', None, True), # right double quote
        ('\xe2\x94\xe2\x94\x82', '|', False), # pipe
        ('\xe2\x8e\xe2\x8e\xaa', None, False), # pipe 2
        ('\xe2\x80\xe2\x80\x90', None, True), # hyphen
        ('\xe2\x80\xe2\x80\x94', None, True), # hyphen 2
        ('\xe2\x80\xc2\xbd', None, True), # half
        ('\xe2\x88\xe2\x88\x97', None, True), # asterisk
        ('\xe2\x86\xe2\x86\x92', None, True), # right arrow
        ('\xe2\x88\xe2\x88\x92', None, True), # minus sign
        ('\xe2\x80\xe2\x80\x93', None, True), # en dash
        ('\xe2\x80\xe2\x80\xb2', None, False), # prime
        ('\xe2\x88\xe2\x88\xbc', None, False), # tilde operator
        ('\xe2\x86\xe2\x86\xb5', None, False), # downwards arrow with corner leftwards
        ('\xef\xbf\xef\xbf\xbd', None, False) # replacement char
        ]

_replacements = []

for searchfor, replacewith, underline in _replacementsprefix:
    if replacewith is None:
        replacewith = searchfor[2:]
    _replacements.append((searchfor, replacewith))
    if underline:
        x = list(replacewith)
        x.insert(1, '</u>')
        x = ''.join(x)
        _replacements.append((x, '%s</u>' % replacewith))

_replacementsnoprefix = ['\xc2\xb7', # bullet
        '\xc2\xb4', # apostrophe
        '\xc2\xa0', # no break space
        '\xc3\xb8', '\xe4\xbd\xa0', '\xe5\xa5\xbd', # gibberish
        '\xc2\xa7', # section sign
        '\xef\xbf\xbd', # replacement char
        '\xc2\xa4',  # latin small letter a with diaeresis
        '\xc3\xa4', # latin small letter a with diaeresis
        '\xc4\xa4',  # latin small letter a with diaeresis
        '\xc3\xaa',  # latin small letter e with circumflex
        ]

for s in _replacementsnoprefix:
    x = list(s)
    x.insert(1, '</u>')
    x = ''.join(x)
    _replacements.append((x, '%s</u>' % s))

_href = re.compile(r'<a href="file:///[^\?]*\?([^\(]*)\(([^\)]*)\)">')
_section = re.compile(r'<b>([^<]+)</b>')

def _parsetext(lines):
    paragraphlines = []
    section = None
    i = 0
    for l in lines:
        l = re.sub(_href, r'<a href="http://manpages.ubuntu.com/manpages/precise/en/man\2/\1.\2.html">', l)
        for lookfor, replacewith in _replacements:
            l = re.sub(lookfor, replacewith, l)
        # confirm the line is valid utf8
        lreplaced = l.decode('utf8', 'ignore').encode('utf8')
        if lreplaced != l:
            logger.error('line %r contains invalid utf8', l)
            l = lreplaced
            raise ValueError
        if l.startswith('<b>'): # section
            section = re.sub(_section, r'\1', l)
        else:
            foundsection = False
            if l.strip().startswith('<b>'):
                inside, outside = bold(l.strip())
                if not outside and inside[-1][-1] == ':':
                    foundsection = True
                    section = ' '.join(inside)[:-1]
            if not foundsection:
                if not l.strip() and paragraphlines:
                    yield store.paragraph(i, '\n'.join(paragraphlines), section, False)
                    i += 1
                    paragraphlines = []
                elif l.strip():
                    paragraphlines.append(l)
    if paragraphlines:
        yield store.paragraph(i, '\n'.join(paragraphlines), section, False)

def _parsesynopsis(base, synopsis):
    '''
    >>> _parsesynopsis('/a/b/c', '/a/b/c: "p-r+o++g - foo bar."')
    ('p-r+o++g', 'foo bar')
    '''
    synopsis = synopsis[len(base)+3:-1]
    if synopsis[-1] == '.':
        synopsis = synopsis[:-1]
    return SPLITSYNOP.match(synopsis).groups()

class manpage(object):
    '''read the man page at path by executing w3mman2html.cgi and find its
    synopsis with lexgrog

    since some man pages share the same name (different versions), each
    alias of a man page has a score that's determined in this simple fashion:
    - name of man page source file is given a score of 10
    - all other names found for a particular man page are given a score of 1
      (other names are found by scanning the output of lexgrog)
    '''
    def __init__(self, path):
        self.path = path
        self.shortpath = os.path.basename(self.path)
        self.name = extractname(self.path)
        self.aliases = set([self.name])
        self.synopsis = None
        self.paragraphs = None
        self._text = None

    def read(self):
        '''Read the content from a local manpage file and store it in usable formats
        on the class instance.'''
        cmd = [config.MAN2HTML, urllib.urlencode({'local' : os.path.abspath(self.path)})]
        logger.info('executing %r', ' '.join(cmd))
        self._text = subprocess.check_output(cmd, stderr=devnull, env=ENV)
        try:
            self.synopsis = subprocess.check_output(['lexgrog', self.path], stderr=devnull).rstrip()
        except subprocess.CalledProcessError:
            logger.error('failed to extract synopsis for %s', self.name)

    def parse(self):
        self.paragraphs = list(_parsetext(self._text.splitlines()[7:-3]))
        if not self.paragraphs:
            raise errors.EmptyManpage(self.shortpath)
        if self.synopsis:
            self.synopsis = [_parsesynopsis(self.path, l) for l in self.synopsis.splitlines()]

            # figure out aliases from the synopsis
            d = collections.OrderedDict()
            for prog, text in self.synopsis:
                d.setdefault(text, []).append(prog)
            text, progs = d.items()[0]
            self.synopsis = text
            self.aliases.update(progs)
        self.aliases.remove(self.name)

        # give the name of the man page the highest score
        self.aliases = [(self.name, 10)] + [(x, 1) for x in self.aliases]
