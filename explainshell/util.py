import itertools
from operator import itemgetter


def consecutive(ln, fn):
    """yield consecutive items from l that fn returns True for them

    >>> even = lambda x: x % 2 == 0
    >>> list(consecutive([], even))
    []
    >>> list(consecutive([1], even))
    [[1]]
    >>> list(consecutive([1, 2], even))
    [[1], [2]]
    >>> list(consecutive([2, 4], even))
    [[2, 4]]
    >>> list(consecutive([1, 2, 4], even))
    [[1], [2, 4]]
    >>> list(consecutive([1, 2, 4, 5, 7, 8, 10], even))
    [[1], [2, 4], [5], [7], [8, 10]]
    """
    it = iter(ln)
    ll = []
    try:
        while True:
            x = it.next()
            if fn(x):
                ll.append(x)
            else:
                if ll:
                    yield ll
                    ll = []
                yield [x]
    except StopIteration:
        if ll:
            yield ll


def group_continuous(l, key=None):
    """
    >>> list(groupcontinuous([1, 2, 4, 5, 7, 8, 10]))
    [[1, 2], [4, 5], [7, 8], [10]]
    >>> list(groupcontinuous(range(5)))
    [[0, 1, 2, 3, 4]]
    """
    if key is None:
        key = lambda x: x
    for k, g in itertools.groupby(enumerate(l), lambda ix: ix[0] - key(ix[1])):
        yield map(itemgetter(1), g)


def topo_sorted(graph, parents):
    """
    Returns vertices of a DAG in topological order.

    Arguments:
    graph -- vertices of a graph to be topo_sorted
    parents -- function (vertex) -> vertices to proceed
               given vertex in output
    """
    result = []
    used = set()

    def use(v, top):
        if id(v) in used:
            return
        for parent in parents(v):
            if parent is top:
                raise ValueError("graph is cyclical", graph)
            use(parent, v)
        used.add(id(v))
        result.append(v)

    for v in graph:
        use(v, v)
    return result


def pairwise(iterable):
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)


class Peekable:
    """
    >>> it = Peekable(iter('abc'))
    >>> it.index, it.peek(), it.index, it.peek(), it.next(), it.index, it.peek(), it.next(), it.next(), it.index
    (0, 'a', 0, 'a', 'a', 1, 'b', 'b', 'c', 3)
    >>> it.peek()
    Traceback (most recent call last):
      File "<stdin>", line 1, in ?
    StopIteration
    >>> it.peek()
    Traceback (most recent call last):
      File "<stdin>", line 1, in ?
    StopIteration
    >>> it.next()
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

    def next(self):
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


class PropertyCache:
    def __init__(self, func):
        self.func = func
        self.name = func.__name__

    def __get__(self, obj, type=None):
        result = self.func(obj)
        self.cache_value(obj, result)
        return result

    def cache_value(self, obj, value):
        setattr(obj, self.name, value)
