import os
import subprocess
import re
import logging
import collections

SPLIT_SYNOP = re.compile(r"([^ ]+) - (.*)$")

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


def _parse_synopsis(base, synopsis):
    """
    >>> _parse_synopsis('/a/b/c', '/a/b/c: "p-r+o++g - foo bar."')
    ('p-r+o++g', 'foo bar')
    """
    synopsis = synopsis[len(base) + 3 : -1]
    if synopsis[-1] == ".":
        synopsis = synopsis[:-1]

    if not SPLIT_SYNOP.match(synopsis):
        return []

    return SPLIT_SYNOP.match(synopsis).groups()


def _run_lexgrog(gz_path: str, name: str) -> str:
    """Run lexgrog on a man page and return the raw synopsis string.

    Raises FileNotFoundError if the file does not exist, and RuntimeError
    if lexgrog fails or produces no output.
    """
    if not os.path.isfile(gz_path):
        raise FileNotFoundError(f"manpage file not found: {gz_path}")
    proc = subprocess.run(
        ["lexgrog", gz_path], capture_output=True, text=True, timeout=300
    )
    if proc.stderr:
        logger.warning("lexgrog stderr for %s: %s", name, proc.stderr)
    if not proc.stdout or not proc.stdout.strip():
        raise RuntimeError(f"lexgrog produced no output for {gz_path}")
    return proc.stdout.rstrip()


def get_synopsis_and_aliases(gz_path: str) -> tuple[str | None, list[tuple[str, int]]]:
    """Extract synopsis text and alias list from a man page via lexgrog.

    Returns (synopsis, aliases) where synopsis is a string or None and aliases
    is a list of (name, score) tuples.
    """
    name = extract_name(gz_path)
    raw_synopsis = _run_lexgrog(gz_path, name)

    synopsis = None
    aliases = [(name, 10)]

    lines = raw_synopsis.splitlines()
    parsed = [_parse_synopsis(gz_path, line) for line in lines if line.strip()]
    parsed = [p for p in parsed if p]
    if parsed:
        d = collections.OrderedDict()
        for prog, text in parsed:
            d.setdefault(text, []).append(prog)
        text, progs = list(dict(d).items())[0]
        synopsis = text
        alias_names = set(progs)
        alias_names.discard(name)
        aliases = [(name, 10)] + [(x, 1) for x in alias_names]

    return synopsis, aliases
