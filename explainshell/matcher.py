import collections, logging
from explainshell import options, errors, util, parser

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
        self.manpage = None
        self._prevoption = self._currentoption = None
        self.matches = []

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
        self.manpage = self.mps[0]
        endpos = wordnode.pos[1]
        nextwordnode = parser.findfirstkind(parts, 'word')

        if self.manpage.multicommand and nextwordnode:
            try:
                multi = '%s %s' % (wordnode.word, nextwordnode.word)
                logger.info('%r is a multicommand, trying to get another token and look up %r', self.manpage, multi)
                self.mps = self.findmanpages(multi)
                self.manpage = self.mps[0]
                idx = 0 # parts.index(nextwordnode)
                for p in parts:
                    if p is nextwordnode:
                        break
                    idx += 1
                assert idx < len(parts)
                parts.pop(idx)
                endpos = nextwordnode.pos[1]
            except errors.ProgramDoesNotExist:
                logger.info('no manpage %r for multicommand %r', multi, self.manpage)
        self.matches.append(matchresult(node.pos[0], endpos, self.manpage.synopsis, None))

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
                    self.matches += m
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

        self.matches = self._mergeunknowns(self.matches)
        self.matches = self._mergeadjacent(self.matches)

        # add matchresult.match to existing matches
        for i, m in enumerate(self.matches):
            assert m.end <= len(self.s), '%d %d' % (m.end, len(self.s))
            self.matches[i] = matchresult(m.start, m.end, m.text, self.s[m.start:m.end])

        r = [(self.manpage.name, self.matches)]
        for mp in self.mps[1:]:
            r.append((mp, None))
        return r

    def _mergeadjacent(self, matches):
        merged = []
        it = util.peekable(iter(matches))
        curr = it.next()
        while it.hasnext():
            next = it.peek()
            if curr.text != next.text:
                merged.append(curr)
                curr = it.next()
            else:
                logger.debug('merging adjacent identical matches %d and %d', it.index - 1, it.index)
                it.next()
                curr = matchresult(curr.start, next.end, curr.text, curr.match)
        merged.append(curr)
        return merged

    def _mergeunknowns(self, matches):
        merged = []
        for l in util.consecutive(matches, lambda m: m.unknown):
            if len(l) == 1:
                merged.append(l[0])
            else:
                start = l[0].start
                end = l[-1].end
                merged.append(matchresult(start, end, None, None))
        return merged
