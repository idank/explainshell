import collections, logging, itertools
from explainshell import options, errors, util, parser

class matchgroup(object):
    '''a class to group matchresults together

    we group all shell results in one group and create a new group for every
    command'''
    def __init__(self, name):
        self.name = name
        self.results = []

class matchresult(collections.namedtuple('matchresult', 'start end text match')):
    @property
    def unknown(self):
        return self.text is None

logger = logging.getLogger(__name__)

class matcher(parser.NodeVisitor):
    '''parse a command line and return a list of matchresults describing
    each token.

    passing in a section causes the store to do look up the command in a
    specific section'''
    def __init__(self, s, store, section=None):
        self.s = s
        self.section = section
        self.store = store
        self._prevoption = self._currentoption = None
        self.groups = [matchgroup('shell')]

    @property
    def matches(self):
        '''return the list of results from the most recently created group'''
        return self.groups[-1].results

    @property
    def allmatches(self):
        return list(itertools.chain.from_iterable(g.results for g in self.groups))

    @property
    def manpage(self):
        return self.groups[-1].manpage

    def find_option(self, opt):
        self._currentoption = self.manpage.find_option(opt)
        logger.debug('looking up option %r, got %r', opt, self._currentoption)
        return self._currentoption

    def findmanpages(self, prog):
        logger.info('looking up %r in store', prog)
        manpages = self.store.findmanpage(prog, self.section)
        logger.info('found %r in store, got: %r, using %r', prog, manpages, manpages[0])
        return manpages

    def unknown(self, token, start, end):
        logger.debug('nothing to do with token %r', token)
        return matchresult(start, end, None, None)

    def visitcommand(self, node, parts):
        assert parts
        wordnode = parts.pop(0)
        self.mps = self.findmanpages(wordnode.word)
        manpage = self.mps[0]
        endpos = wordnode.pos[1]
        nextwordnode = parser.findfirstkind(parts, 'word')

        if manpage.multicommand and nextwordnode:
            try:
                multi = '%s %s' % (wordnode.word, nextwordnode.word)
                logger.info('%r is a multicommand, trying to get another token and look up %r', manpage, multi)
                self.mps = self.findmanpages(multi)
                manpage = self.mps[0]
                idx = 0 # parts.index(nextwordnode)
                for p in parts:
                    if p is nextwordnode:
                        break
                    idx += 1
                assert idx < len(parts)
                parts.pop(idx)
                endpos = nextwordnode.pos[1]
            except errors.ProgramDoesNotExist:
                logger.info('no manpage %r for multicommand %r', multi, manpage)

        # create a new matchgroup for the current command
        name = 'command%d' % len([g for g in self.groups if g.name.startswith('command')])
        mg = matchgroup(name)
        mg.manpage = manpage
        mg.others = self.mps
        self.groups.append(mg)

        self.matches.append(matchresult(node.pos[0], endpos, manpage.synopsis, None))

    def visitword(self, node, word):
        def attemptfuzzy(chars):
            m = []
            if chars[0] == '-':
                tokens = [chars[0:2]] + list(chars[2:])
                considerarg = True
            else:
                tokens = list(chars)
                considerarg = False

            pos = node.pos[0]
            for t in tokens:
                op = t if t[0] == '-' else '-' + t
                option = self.find_option(op)
                if option:
                    if considerarg and not m and option.expectsarg:
                        logger.info('option %r expected an arg, taking the rest too', option)
                        return [matchresult(pos, pos+len(chars), option.text, None)]

                    mr = matchresult(pos, pos+len(t), option.text, None)
                    m.append(mr)
                else:
                    m.append(self.unknown(t, pos, pos+len(t)))
                pos += len(t)
            return m

        logger.info('trying to match token: %r', word)

        self._prevoption = self._currentoption
        if word.startswith('--'):
            word = word.split('=', 1)[0]
        option = self.find_option(word)
        if option:
            logger.info('found an exact match for %r: %r', word, option)
            mr = matchresult(node.pos[0], node.pos[1], option.text, None)
            self.matches.append(mr)
        else:
            word = node.word
            if word != '-' and word.startswith('-') and not word.startswith('--'):
                logger.debug('looks like a short option')
                if len(word) > 2:
                    logger.info("trying to split it up")
                    self.matches.extend(attemptfuzzy(word))
                else:
                    self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
            elif self._prevoption and self._prevoption.expectsarg:
                logger.info("previous option possibly expected an arg, and we can't"
                        " find an option to match the current token, assuming it's an arg")
                ea = self._prevoption.expectsarg
                possibleargs = ea if isinstance(ea, list) else []
                take = True
                if possibleargs and word not in possibleargs:
                    take = False
                    logger.info('token %r not in list of possible args %r for %r',
                                word, possibleargs, self._prevoption)
                if take:
                    pmr = self.matches[-1]
                    mr = matchresult(pmr.start, node.pos[1], pmr.text, None)
                    self.matches[-1] = mr
                else:
                    self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
            elif self.manpage.partialmatch:
                logger.info('attemping to do a partial match')

                m = attemptfuzzy(word)
                if any(mm.unknown for mm in m):
                    logger.info('one of %r was unknown', word)
                    self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
                else:
                    self.matches.extend(m)
            elif self.manpage.arguments:
                d = self.manpage.arguments
                k = list(d.keys())[0]
                logger.info('got arguments, using %r', k)
                text = d[k]
                mr = matchresult(node.pos[0], node.pos[1], text, None)
                self.matches.append(mr)
            else:
                self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))

    def match(self):
        logger.info('matching string %r', self.s)
        self.ast = parser.parse_command_line(self.s)
        self.visit(self.ast)

        def debugmatch():
            s = '\n'.join(['%d) %r = %r' % (i, self.s[m.start:m.end], m.text) for i, m in enumerate(self.matches)])
            return s

        logger.debug('%r matches:\n%s', self.s, debugmatch())

        self._markunparsedunknown()

        # fix each matchgroup seperately
        for group in self.groups:
            if group.results:
                group.results = self._mergeadjacent(group.results)

                # add matchresult.match to existing matches
                for i, m in enumerate(group.results):
                    assert m.end <= len(self.s), '%d %d' % (m.end, len(self.s))
                    group.results[i] = matchresult(m.start, m.end, m.text, self.s[m.start:m.end])

        # take all matches for now
        r = [(self.manpage.name, sorted(self.allmatches, key=lambda mr: mr.start))]
        for mp in self.mps[1:]:
            r.append((mp, None))
        return r

    def _markunparsedunknown(self):
        '''the parser may leave a remainder at the end of the string if it doesn't
        match any of the rules, mark them as unknowns'''
        parsed = [False]*len(self.s)
        for i in range(len(parsed)):
            # whitespace is always 'unparsed'
            if self.s[i].isspace():
                parsed[i] = True
            else:
                # go over all existing matches to see if we've covered the
                # current position
                for start, end, _, _ in self.allmatches:
                    if start <= i < end:
                        parsed[i] = True
                        break
            if not parsed[i]:
                # add unparsed results to the 'shell' group
                self.groups[0].results.append(self.unknown(self.s[i], i, i+1))

        # there are no overlaps, so sorting by the start is enough
        self.groups[0].results.sort(key=lambda mr: mr.start)

    def _resultindex(self):
        '''return a mapping of matchresults to their index among all
        matches, sorted by the start position of the matchresult'''
        d = {}
        i = 0
        for result in sorted(self.allmatches, key=lambda mr: mr.start):
            d[result] = i
            i += 1
        return d

    def _mergeadjacent(self, matches):
        merged = []
        resultindex = self._resultindex()
        sametext = itertools.groupby(matches, lambda m: m.text)
        for text, ll in sametext:
            for l in util.groupcontinuous(ll, key=lambda m: resultindex[m]):
                if len(l) == 1:
                    merged.append(l[0])
                else:
                    start = l[0].start
                    end = l[-1].end
                    endindex = resultindex[l[-1]]
                    for mr in l:
                        del resultindex[mr]
                    merged.append(matchresult(start, end, text, None))
                    resultindex[merged[-1]] = endindex
        return merged
