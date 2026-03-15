import glob
import itertools
import os
from operator import itemgetter


def group_continuous(items, key=None):
    """
    >>> list(group_continuous([1, 2, 4, 5, 7, 8, 10]))
    [[1, 2], [4, 5], [7, 8], [10]]
    >>> list(group_continuous(range(5)))
    [[0, 1, 2, 3, 4]]
    """
    if key is None:

        def identity(value):
            return value

        key_func = identity
    else:
        key_func = key
    for _, grouped in itertools.groupby(
        enumerate(items), lambda ix: ix[0] - key_func(ix[1])
    ):
        yield list(map(itemgetter(1), grouped))


class Peekable:
    """
    >>> it = Peekable(iter('abc'))
    >>> it.index, it.peek(), it.index, it.peek(), next(it), it.index, it.peek(), next(it), next(it), it.index
    (0, 'a', 0, 'a', 'a', 1, 'b', 'b', 'c', 3)
    >>> it.peek()
    Traceback (most recent call last):
      File "<stdin>", line 1, in ?
    StopIteration
    >>> it.peek()
    Traceback (most recent call last):
      File "<stdin>", line 1, in ?
    StopIteration
    >>> next(it)
    Traceback (most recent call last):
      File "<stdin>", line 1, in ?
    StopIteration
    """

    def __init__(self, it):
        self.it = it
        self._peeked = False
        self._peek_value = None
        self._idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._peeked:
            self._peeked = False
            self._idx += 1
            return self._peek_value
        n = next(self.it)
        self._idx += 1
        return n

    def has_next(self):
        try:
            self.peek()
            return True
        except StopIteration:
            return False

    def peek(self):
        if self._peeked:
            return self._peek_value
        else:
            self._peek_value = next(self.it)
            self._peeked = True
            return self._peek_value

    @property
    def index(self):
        """return the index of the next item returned by next()"""
        return self._idx


def name_section(path):
    assert ".gz" not in path
    name, section = path.rsplit(".", 1)
    return name, section


def collect_gz_files(paths: list[str]) -> list[str]:
    """Expand a list of files/directories into absolute .gz file paths."""
    result: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            result.extend(
                sorted(glob.glob(os.path.join(path, "**", "*.gz"), recursive=True))
            )
        else:
            result.append(path)
    return [os.path.abspath(p) for p in result]


def fmt_tokens(n: int) -> str:
    """Format token count for display (e.g. 878K, 1.8M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)
