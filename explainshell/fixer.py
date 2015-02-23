import textwrap, logging

from explainshell import util

class basefixer(object):
    '''The base fixer class which other fixers inherit from.

    Subclasses override the base methods in order to fix manpage content during
    different parts of the parsing/classifying/saving process.'''
    runbefore = []
    runlast = False

    def __init__(self, mctx):
        self.mctx = mctx
        self.run = True
        self.logger = logging.getLogger(self.__class__.__name__)

    def pre_get_raw_manpage(self):
        pass

    def pre_parse_manpage(self):
        pass

    def post_parse_manpage(self):
        pass

    def pre_classify(self):
        pass

    def post_classify(self):
        pass

    def post_option_extraction(self):
        pass

    def pre_add_manpage(self):
        pass

fixerscls = []
fixerspriority = {}

class runner(object):
    '''The runner coordinates the fixers.'''
    def __init__(self, mctx):
        self.mctx = mctx
        self.fixers = [f(mctx) for f in fixerscls]

    def disable(self, name):
        before = len(self.fixers)
        self.fixers = [f for f in self.fixers if f.__class__.__name__ != name]
        if before == len(self.fixers):
            raise ValueError('fixer %r not found' % name)

    def _fixers(self):
        return (f for f in self.fixers if f.run)

    def pre_get_raw_manpage(self):
        for f in self._fixers():
            f.pre_get_raw_manpage()

    def pre_parse_manpage(self):
        for f in self._fixers():
            f.pre_parse_manpage()

    def post_parse_manpage(self):
        for f in self._fixers():
            f.post_parse_manpage()

    def pre_classify(self):
        for f in self._fixers():
            f.pre_classify()

    def post_classify(self):
        for f in self._fixers():
            f.post_classify()

    def post_option_extraction(self):
        for f in self._fixers():
            f.post_option_extraction()

    def pre_add_manpage(self):
        for f in self._fixers():
            f.pre_add_manpage()

def register(fixercls):
    fixerscls.append(fixercls)
    for f in fixercls.runbefore:
        if not hasattr(f, '_parents'):
            f._parents = []
        f._parents.append(fixercls)
    return fixercls

@register
class bulletremover(basefixer):
    '''remove list bullets from paragraph start, see mysqlslap.1'''
    def post_parse_manpage(self):
        toremove = []
        for i, p in enumerate(self.mctx.manpage.paragraphs):
            try:
                idx = p.text.index('\xc2\xb7')
                p.text = p.text[:idx] + p.text[idx+2:]
                if not p.text.strip():
                    toremove.append(i)
            except ValueError:
                pass
        for i in reversed(toremove):
            del self.mctx.manpage.paragraphs[i]

@register
class leadingspaceremover(basefixer):
    '''go over all known option paragraphs and remove their leading spaces
    by the amount of spaces in the first line'''

    def post_option_extraction(self):
        for i, p in enumerate(self.mctx.manpage.options):
            text = self._removewhitespace(p.text)
            p.text = text

    def _removewhitespace(self, text):
        '''
        >>> f = leadingspaceremover(None)
        >>> f._removewhitespace(' a\\n  b ')
        'a\\n b'
        >>> f._removewhitespace('\\t a\\n\\t \\tb')
        'a\\n\\tb'
        '''
        return textwrap.dedent(text).rstrip()

@register
class tarfixer(basefixer):
    def __init__(self, *args):
        super(tarfixer, self).__init__(*args)
        self.run = self.mctx.name == 'tar'

    def pre_add_manpage(self):
        self.mctx.manpage.partialmatch = True

@register
class paragraphjoiner(basefixer):
    runbefore = [leadingspaceremover]
    maxdistance = 5

    def post_option_extraction(self):
        options = [p for p in self.mctx.manpage.paragraphs if p.is_option]
        self._join(self.mctx.manpage.paragraphs, options)

    def _join(self, paragraphs, options):
        def _paragraphsbetween(op1, op2):
            assert op1.idx < op2.idx
            r = []
            start = None
            for i, p in enumerate(paragraphs):
                if op1.idx < p.idx < op2.idx:
                    if not r:
                        start = i
                    r.append(p)
            return r, start

        totalmerged = 0
        for curr, next in util.pairwise(options):
            between, start = _paragraphsbetween(curr, next)
            if curr.section == next.section and 1 <= len(between) < self.maxdistance:
                self.logger.info('merging paragraphs %d through %d (inclusive)', curr.idx, next.idx-1)
                newdesc = [curr.text.rstrip()]
                newdesc.extend([p.text.rstrip() for p in between])
                curr.text = '\n\n'.join(newdesc)
                del paragraphs[start:start+len(between)]
                totalmerged += len(between)
        return totalmerged

@register
class optiontrimmer(basefixer):
    runbefore = [paragraphjoiner]

    d = {'git-rebase' : (50, -1)}

    def __init__(self, mctx):
        super(optiontrimmer, self).__init__(mctx)
        self.run = self.mctx.name in self.d

    def post_classify(self):
        start, end = self.d[self.mctx.name]
        classifiedoptions = [p for p in self.mctx.manpage.paragraphs if p.is_option]
        assert classifiedoptions
        if end == -1:
            end = classifiedoptions[-1].idx
        else:
            assert start > end

        for p in classifiedoptions:
            if not (start <= p.idx <= end):
                p.is_option = False
                self.logger.info('removing option %r', p)

def _parents(fixercls):
    p = getattr(fixercls, '_parents', [])
    last = fixercls.runlast

    if last and p:
        raise ValueError("%s can't be last and also run before someone else" % fixercls.__name__)

    if last:
        return [f for f in fixerscls if f is not fixercls]
    return p

fixerscls = util.toposorted(fixerscls, _parents)
