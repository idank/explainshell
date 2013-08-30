import collections, logging
from explainshell import options, errors, util

class matchresult(collections.namedtuple('matchresult', 'start end text match')):
    @property
    def unknown(self):
        return self.text is None

logger = logging.getLogger(__name__)

class matcher(object):
    '''parse a command line and return a list of matchresults describing
    each token.

    passing in a section causes the store to do look up the command in a
    specific section'''
    def __init__(self, s, store, section=None):
        self.s = s
        self.section = section
        self.store = store
        self.manpage = None
        self.pos = 0
        self._prevoption = self._currentoption = None

    def find_option(self, opt):
        self._currentoption = self.manpage.find_option(opt)
        logger.debug('looking up option %r, got %r', opt, self._currentoption)
        return self._currentoption

    def findmanpages(self, prog):
        logger.info('looking up %r in store', prog)
        manpages = self.store.findmanpage(prog, self.section)
        logger.info('found %r in store, got: %r, using %r', prog, manpages, manpages[0])
        return manpages

    def unknown(self, token, endpos=None):
        if endpos is None:
            endpos = self.ts.endpos
        logger.debug('nothing to do with token %r', token)
        return matchresult(self.pos, endpos, None, None)

    def nexttoken(self):
        self.ts = self.tokens.next()
        return self.ts

    def match(self):
        '''parse s and return a list of matchresult

        match works as follows:
        - tokenize the string using options.tokenize
        - look up a man page that matches the first token
        - check if the man page has 'subcommands', e.g. git commit, if so
          try to find a man page for the first two tokens
        - iterate all tokens
          - search the token as is
          - if the token is a short option (-abc) try to look up each option
            individually
          - if the previous match expected an arg, merge this token with
            the previous one
          - partialmatch: if this man page allows options to start without '-',
            try to match all characters individually (e.g. tar xzvf)
          - check if this man page has any positional arguments
          - mark this token as unknown

        after we finish processing all tokens, we:
        - merge unknown consecutive matches to become one matchresult
        - merge adjacent matchresults that have the same help text (e.g. if we
          had -vvv)
        '''
        logger.info('matching string %r', self.s)
        self.tokens = util.peekable(options.tokenize(self.s))
        #logger.info('tokenized %r to %s', self.s, [t[1] for t in self.tokens])
        self.ts = None

        def attempt_fuzzy(chars):
            m = []
            if chars[0] == '-':
                tokens = [chars[0:2]] + list(chars[2:])
                considerarg = True
            else:
                tokens = list(chars)
                considerarg = False

            oldp = self.pos
            for t in tokens:
                op = t if t[0] == '-' else '-' + t
                option = self.find_option(op)
                if option:
                    if considerarg and not m and option.expectsarg:
                        logger.info('option %r expected an arg, taking the rest too', option)
                        return [matchresult(self.pos, self.pos+len(chars), option.text, None)]

                    mr = matchresult(self.pos, self.pos+len(t), option.text, None)
                    m.append(mr)
                else:
                    m.append(self.unknown(t, self.pos+len(t)))
                self.pos += len(t)
            self.pos = oldp
            return m

        self.pos, endpos, token = self.nexttoken()
        mps = self.findmanpages(token)
        self.manpage = mps[0]
        if self.manpage.multicommand and self.tokens.hasnext():
            try:
                multi = '%s %s' % (token, self.tokens.peek()[2])
                logger.info('%r is a multicommand, trying to get another token and look up %r', self.manpage, multi)
                mps = self.findmanpages(multi)
                self.manpage = mps[0]
                self.nexttoken()
                token = multi
            except errors.ProgramDoesNotExist:
                logger.info('no manpage %r for multicommand %r', multi, self.manpage)

        option = None
        matches = []
        matches.append(matchresult(0, len(token), self.manpage.synopsis, None))

        while self.tokens.hasnext():
            self.pos, endpos, token = self.nexttoken()
            logger.info('trying to match token: %r', token)

            self._prevoption = self._currentoption
            option = self.find_option(token)
            if option:
                logger.info('found an exact match for %r: %r', token, option)
                mr = matchresult(self.pos, self.ts.endpos, option.text, None)
                matches.append(mr)
            else:
                if token != '-' and token.startswith('-') and not token.startswith('--'):
                    logger.debug('looks like a short option')
                    if len(token) > 2:
                        logger.info("trying to split it up")
                        matches.extend(attempt_fuzzy(token))
                        self.pos += len(token)
                    else:
                        matches.append(self.unknown(token))
                elif self._prevoption and self._prevoption.expectsarg:
                    logger.info("previous option possibly expected an arg, and we can't"
                            " find an option to match the current token, assuming it's an arg")
                    ea = self._prevoption.expectsarg
                    possibleargs = ea if isinstance(ea, list) else []
                    take = True
                    if possibleargs and token not in possibleargs:
                        take = False
                        logger.info('token %r not in list of possible args %r for %r',
                                    token, possibleargs, self._prevoption)
                    if take:
                        pmr = matches[-1]
                        mr = matchresult(pmr.start, self.ts.endpos, pmr.text, None)
                        matches[-1] = mr
                    else:
                        matches.append(self.unknown(token))
                elif self.manpage.partialmatch:
                    logger.info('attemping to do a partial match')

                    m = attempt_fuzzy(token)
                    if any(mm.unknown for mm in m):
                        logger.info('one of %r was unknown', token)
                        matches.append(self.unknown(token))
                    else:
                        matches += m
                        self.pos += len(token)
                elif self.manpage.arguments:
                    d = self.manpage.arguments
                    k = list(d.keys())[0]
                    logger.info('got arguments, using %r', k)
                    text = d[k]
                    mr = matchresult(self.pos, self.ts.endpos, text, None)
                    matches.append(mr)
                else:
                    matches.append(self.unknown(token))

        def debugmatch():
            s = '\n'.join(['%d) %r = %r' % (i, self.s[m.start:m.end], m.text) for i, m in enumerate(matches)])
            return s

        logger.debug('%r matches:\n%s', self.s, debugmatch())

        matches = self._mergeunknowns(matches)
        matches = self._mergeadjacent(matches)

        # add matchresult.match to existing matches
        for i, m in enumerate(matches):
            assert m.end <= len(self.s), '%d %d' % (m.end, len(self.s))
            matches[i] = matchresult(m.start, m.end, m.text, self.s[m.start:m.end])

        r = [(self.manpage.name, matches)]
        for mp in mps[1:]:
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
