import itertools
from operator import itemgetter

def consecutive(l, fn):
    '''yield consecutive items from l that fn returns True for them

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
    '''
    it = iter(l)
    ll = []
    try:
        while True:
            x = next(it)
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

def groupcontinuous(l, key=None):
    '''
    >>> list(groupcontinuous([1, 2, 4, 5, 7, 8, 10]))
    [[1, 2], [4, 5], [7, 8], [10]]
    >>> list(groupcontinuous(range(5)))
    [[0, 1, 2, 3, 4]]
    '''
    if key is None:
        key = lambda x: x
    for k, g in itertools.groupby(enumerate(l), lambda i_x: i_x[0]-key(i_x[1])):
        yield list(map(itemgetter(1), g))

def toposorted(graph, parents):
    """
    Returns vertices of a DAG in topological order.

    Arguments:
    graph -- vetices of a graph to be toposorted
    parents -- function (vertex) -> vertices to preceed
               given vertex in output
    """
    result = []
    used = set()
    def use(v, top):
        if id(v) in used:
            return
        for parent in parents(v):
            if parent is top:
                raise ValueError('graph is cyclical', graph)
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

class peekable(object):
    '''
    >>> it = peekable(iter('abc'))
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
    '''
    def __init__(self, it):
        self.it = it
        self._peeked = False
        self._peekvalue = None
        self._idx = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self._peeked:
            self._peeked = False
            self._idx += 1
            return self._peekvalue
        n = next(self.it)
        self._idx += 1
        return n
    def hasnext(self):
        try:
            self.peek()
            return True
        except StopIteration:
            return False
    def peek(self):
        if self._peeked:
            return self._peekvalue
        else:
            self._peekvalue = next(self.it)
            self._peeked = True
            return self._peekvalue
    @property
    def index(self):
        '''return the index of the next item returned by next()'''
        return self._idx

def namesection(path):
    assert '.gz' not in path
    name, section = path.rsplit('.', 1)
    return name, section

class propertycache(object):
    def __init__(self, func):
        self.func = func
        self.name = func.__name__

    def __get__(self, obj, type=None):
        result = self.func(obj)
        self.cachevalue(obj, result)
        return result

    def cachevalue(self, obj, value):
        setattr(obj, self.name, value)
