import os
import subprocess
import re
import logging
import collections
import urllib

from explainshell import config, store, errors

devnull = open(os.devnull, "w")
SPLIT_SYNOP = re.compile(r"([^ ]+) - (.*)$")

ENV = dict(os.environ)
ENV["W3MMAN_MAN"] = "man --no-hyphenation"
ENV["MAN_KEEP_FORMATTING"] = "1"
ENV["MANWIDTH"] = "115"
ENV["LC_ALL"] = "en_US.UTF-8"

logger = logging.getLogger(__name__)


def extract_name(gz_name):
    """
    >>> extract_name('ab.1.gz')
    'ab'
    >>> extract_name('ab.1.1.gz')
    'ab.1'
    >>> extract_name('ab.1xyz.gz')
    'ab'
    >>> extract_name('ab.1.1xyz.gz')
    'ab.1'
    >>> extract_name('a/b/c/ab.1.1xyz.gz')
    'ab.1'
    """
    if "/" in gz_name:
        gz_name = os.path.basename(gz_name)
    return gz_name.rsplit(".", 2)[0]


def bold(ln_in):
    """
    >>> bold('a')
    ([], ['a'])
    >>> bold('<b>a</b>')
    (['a'], [])
    >>> bold('a<b>b</b>c')
    (['b'], ['a', 'c'])
    >>> bold('<b>first</b> <b>second:</b>')
    (['first', 'second:'], [])
    """
    inside = []
    for m in _section.finditer(ln_in):
        inside.append(m.span(0))

    current = 0
    outside = []
    for start, end in inside:
        outside.append((current, start))
        current = end
    outside.append((current, len(ln_in)))

    inside = [ln_in[s:e] for s, e in inside]
    inside = [s.replace("<b>", "").replace("</b>", "") for s in inside]

    outside = [ln_in[s:e] for s, e in outside]
    outside = [ln for ln in outside if ln and not ln.isspace()]
    return inside, outside


# w3mman2html.cgi (the tool we're using to output html from a man page) does
# some strange escaping which causes it to output invalid utf8. we look these
# up and fix them manually
_rp_prefix = [
    ("\xe2\x80\xe2\x80\x98", None, True),  # left single quote
    ("\xe2\x80\xe2\x80\x99", None, True),  # right single quote
    ("\xe2\x80\xe2\x80\x9c", None, True),  # left double quote
    ("\xe2\x80\xe2\x80\x9d", None, True),  # right double quote
    ("\xe2\x94\xe2\x94\x82", "|", False),  # pipe
    ("\xe2\x8e\xe2\x8e\xaa", None, False),  # pipe 2
    ("\xe2\x80\xe2\x80\x90", None, True),  # hyphen
    ("\xe2\x80\xe2\x80\x94", None, True),  # hyphen 2
    ("\xe2\x80\xc2\xbd", None, True),  # half
    ("\xe2\x88\xe2\x88\x97", None, True),  # asterisk
    ("\xe2\x86\xe2\x86\x92", None, True),  # right arrow
    ("\xe2\x88\xe2\x88\x92", None, True),  # minus sign
    ("\xe2\x80\xe2\x80\x93", None, True),  # en dash
    ("\xe2\x80\xe2\x80\xb2", None, False),  # prime
    ("\xe2\x88\xe2\x88\xbc", None, False),  # tilde operator
    ("\xe2\x86\xe2\x86\xb5", None, False),  # downwards arrow with corner leftwards
    ("\xef\xbf\xef\xbf\xbd", None, False),  # replacement char
]

_replacements = []

for search_for, rp_with, underline in _rp_prefix:
    if rp_with is None:
        rp_with = search_for[2:]
    _replacements.append((search_for, rp_with))
    if underline:
        x = list(rp_with)
        x.insert(1, "</u>")
        x = "".join(x)
        _replacements.append((x, f"{rp_with}</u>"))

_replacements_no_prefix = [
    "\xc2\xb7",  # bullet
    "\xc2\xb4",  # apostrophe
    "\xc2\xa0",  # no break space
    "\xc3\xb8",
    "\xe4\xbd\xa0",
    "\xe5\xa5\xbd",  # gibberish
    "\xc2\xa7",  # section sign
    "\xef\xbf\xbd",  # replacement char
    "\xc2\xa4",  # latin small letter a with diaeresis
    "\xc3\xa4",  # latin small letter a with diaeresis
    "\xc4\xa4",  # latin small letter a with diaeresis
    "\xc3\xaa",  # latin small letter e with circumflex
]

for s in _replacements_no_prefix:
    x = list(s)
    x.insert(1, "</u>")
    x = "".join(x)
    _replacements.append((x, f"{s}</u>"))

_href = re.compile(r'<a href="file:///[^\?]*\?([^\(]*)\(([^\)]*)\)">')
_section = re.compile(r"<b>([^<]+)</b>")


def _parse_text(lines):
    para_lines = []
    section = None
    i = 0
    for ln in lines:
        ln = re.sub(
            _href,
            r'<a href="http://manpages.ubuntu.com/manpages/precise/en/man\2/\1.\2.html">',
            ln,
        )
        for look_for, rp_with in _replacements:
            ln = re.sub(look_for, rp_with, ln)

        # confirm the line is valid utf8
        l_replaced = ln  # .decode("utf8", "ignore").encode("utf8")
        if l_replaced != ln:
            logger.error("line %r contains invalid utf8", ln)
            ln = l_replaced
            raise ValueError
        if ln.startswith("<b>"):  # section
            section = re.sub(_section, r"\1", ln)
        else:
            found_section = False
            if ln.strip().startswith("<b>"):
                inside, outside = bold(ln.strip())
                if not outside and inside[-1][-1] == ":":
                    found_section = True
                    section = " ".join(inside)[:-1]
            if not found_section:
                if not ln.strip() and para_lines:
                    yield store.Paragraph(i, "\n".join(para_lines), section, False)
                    i += 1
                    para_lines = []
                elif ln.strip():
                    para_lines.append(ln)
    if para_lines:
        yield store.Paragraph(i, "\n".join(para_lines), section, False)


def _parse_synopsis(base, synopsis):
    """
    >>> _parse_synopsis('/a/b/c', '/a/b/c: "p-r+o++g - foo bar."')
    ('p-r+o++g', 'foo bar')
    """
    synopsis = synopsis[len(base) + 3: -1]
    if synopsis[-1] == ".":
        synopsis = synopsis[:-1]

    if not SPLIT_SYNOP.match(synopsis):
        return []

    return SPLIT_SYNOP.match(synopsis).groups()


class ManPage:
    """read the man page at path by executing `w3mman2html.cgi` and find it's
    synopsis with `lexgrog`

    since some man pages share the same name (different versions), each
    alias of a man page has a score that's determined in this simple fashion:
    - name of man page source file is given a score of 10
    - all other names found for a particular man page are given a score of 1
      (other names are found by scanning the output of `lexgrog`)
    """

    def __init__(self, path):
        self.path = path
        self.short_path = os.path.basename(self.path)
        self.name = extract_name(self.path)
        self.aliases = {self.name}
        self.synopsis = None
        self.paragraphs = None
        self._text = None

    def read(self):
        """Read the content from a local manpage file and store it in usable formats
        on the class instance."""
        cmd = [config.MAN2HTML, urllib.parse.urlencode({"local": os.path.abspath(self.path)})]
        logger.info("executing %r", " ".join(cmd))
        self._text = ""

        try:
            t_proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300, env=ENV)

            if t_proc.stdout:
                self._text = t_proc.stdout
            if t_proc.stderr:
                logger.error(f"failed to extract text for {self.name} -> w3mman2html.cgi returned: {t_proc.stderr}")
        except Exception as error_msg:
            logger.error(f"failed to extract text for {self.name} -> error: {error_msg}")

        try:
            self.synopsis = ""
            s_proc = subprocess.run(
                ["lexgrog", self.path], capture_output=True, text=True, timeout=300
            )
            if s_proc.stdout:
                self.synopsis = s_proc.stdout.rstrip()
            if s_proc.stderr:
                logger.error(f"failed to extract text for {self.name} -> lexgrog returned: {s_proc.stderr}")
        except subprocess.CalledProcessError:
            logger.error("failed to extract synopsis for %s", self.name)

    def parse(self):
        self.paragraphs = list(_parse_text(self._text.splitlines()[7:-3]))
        if not self.paragraphs:
            raise errors.EmptyManpage(self.short_path)
        if self.synopsis:
            self.synopsis = [
                _parse_synopsis(self.path, s_line) for s_line in self.synopsis.splitlines()
            ]

            # figure out aliases from the synopsis
            d = collections.OrderedDict()
            for prog, text in self.synopsis:
                d.setdefault(text, []).append(prog)
            text, progs = list(dict(d).items())[0]
            self.synopsis = text
            self.aliases.update(progs)
        self.aliases.remove(self.name)

        # give the name of the man page the highest score
        self.aliases = [(self.name, 10)] + [(x, 1) for x in self.aliases]
