import collections, logging, itertools

from explainshell import errors, util, parser, helpconstants

class matchgroup(object):
    '''a class to group matchresults together

    we group all shell results in one group and create a new group for every
    command'''
    def __init__(self, name):
        self.name = name
        self.results = []

    def __repr__(self):
        return '<matchgroup %r with %d results>' % (self.name, len(self.results))

class matchresult(collections.namedtuple('matchresult', 'start end text match')):
    @property
    def unknown(self):
        return self.text is None

logger = logging.getLogger(__name__)

class matcher(parser.NodeVisitor):
    '''parse a command line and return a list of matchresults describing
    each token.
    '''
    def __init__(self, s, store):
        self.s = s.encode('latin1', 'replace')
        self.store = store
        self._prevoption = self._currentoption = None
        self.groups = [matchgroup('shell')]

        # a stack to manage nested command groups: whenever a new simple command
        # is started, we push it to the stack and when it ends we pop it. the second
        # item in the tuple is used to end the top-most command when it is equal
        # to the current word. this is used when a flag starts a new command, e.g.
        # find -exec.
        self.groupstack = [(self.groups[-1], None)]

    @property
    def matches(self):
        '''return the list of results from the most recently created group'''
        return self.groupstack[-1][0].results

    @property
    def allmatches(self):
        return list(itertools.chain.from_iterable(g.results for g in self.groups))

    @property
    def manpage(self):
        return self.groupstack[-1][0].manpage

    def find_option(self, opt):
        self._currentoption = self.manpage.find_option(opt)
        logger.debug('looking up option %r, got %r', opt, self._currentoption)
        return self._currentoption

    def findmanpages(self, prog):
        prog = prog.decode('latin1')
        logger.info('looking up %r in store', prog)
        manpages = self.store.findmanpage(prog)
        logger.info('found %r in store, got: %r, using %r', prog, manpages, manpages[0])
        return manpages

    def unknown(self, token, start, end):
        logger.debug('nothing to do with token %r', token)
        return matchresult(start, end, None, None)

    def visitnegate(self, node):
        helptext = helpconstants.NEGATE
        self.groups[0].results.append(matchresult(node.pos[0], node.pos[1], helptext, None))

    def visitoperator(self, node, op):
        helptext = helpconstants.OPERATORS[op]
        self.groups[0].results.append(matchresult(node.pos[0], node.pos[1], helptext, None))

    def visitpipe(self, node, pipe):
        self.groups[0].results.append(
                matchresult(node.pos[0], node.pos[1], helpconstants.PIPELINES, None))

    def visitredirect(self, node, input, type, output):
        helptext = [helpconstants.REDIRECTION]

        if type in helpconstants.REDIRECTION_KIND:
            helptext.append(helpconstants.REDIRECTION_KIND[type])

        self.groups[0].results.append(
                matchresult(node.pos[0], node.pos[1], '\n\n'.join(helptext), None))

    def visitcompound(self, node, group, list, redirects):
        helptext = helpconstants.COMPOUND[group]
        # we add a matchresult for the start and end of the compound command
        self.groups[0].results.append(matchresult(node.pos[0], node.pos[0]+1, helptext, None))
        self.groups[0].results.append(matchresult(node.pos[1]-1, node.pos[1], helptext, None))

    def visitcommand(self, node, parts):
        assert parts

        # look for the first WordNode, which might not be at parts[0]
        idxwordnode = parser.findfirstkind(parts, 'word')
        if idxwordnode == -1:
            logger.info('no words found in command (probably contains only redirects)')
            return

        self.startcommand(parts, None)
        return len(self.groupstack)

    def visitcommandend(self, node, parts, prevstackdepth):
        # it's possible for visitcommand/end to be called without a command group
        # being pushed if it contains only redirect nodes
        if len(self.groupstack) > 1:
            if prevstackdepth != len(self.groupstack):
                logger.info('group %s is a result of a nested command', self.groupstack[-1])
                self.endcommand()
            self.endcommand()

    def startcommand(self, parts, endword, addunknown=True):
        logger.info('startcommand parts=%r, endword=%r, addunknown=%s',
                    parts, endword, addunknown)
        idxwordnode = parser.findfirstkind(parts, 'word')
        assert idxwordnode != -1

        wordnode = parts.pop(idxwordnode)
        name = 'command%d' % len([g for g in self.groups if g.name.startswith('command')])
        startpos, endpos = wordnode.pos

        try:
            mps = self.findmanpages(wordnode.word)
        except errors.ProgramDoesNotExist, e:
            if addunknown:
                logger.info('no manpage found for %r, marking it unknown', wordnode.word)

                mg = matchgroup(name)
                mg.error = e
                mg.manpage = None
                mg.suggestions = None
                self.groups.append(mg)
                self.groupstack.append((mg, endword))

                self.matches.append(matchresult(startpos, endpos, None, None))

            return False

        manpage = mps[0]
        idxnextwordnode = parser.findfirstkind(parts, 'word')

        if manpage.multicommand and idxnextwordnode != -1:
            nextwordnode = parts[idxnextwordnode]
            try:
                multi = '%s %s' % (wordnode.word, nextwordnode.word)
                logger.info('%r is a multicommand, trying to get another token and look up %r', manpage, multi)
                mps = self.findmanpages(multi)
                manpage = mps[0]
                parts.pop(idxnextwordnode)
                endpos = nextwordnode.pos[1]
            except errors.ProgramDoesNotExist:
                logger.info('no manpage %r for multicommand %r', multi, manpage)

        # create a new matchgroup for the current command
        mg = matchgroup(name)
        mg.manpage = manpage
        mg.suggestions = mps[1:]
        self.groups.append(mg)
        self.groupstack.append((mg, endword))

        self.matches.append(matchresult(startpos, endpos, manpage.synopsis, None))
        return True

    def endcommand(self):
        '''end the most recently created command group by popping it from the
        group stack. groups are created by visitcommand or a nested command'''
        assert len(self.groupstack) >= 2, 'groupstack must contain shell and command groups'
        g = self.groupstack.pop()
        logger.info('ending group %s', g)

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
            prevoption = None
            for i, t in enumerate(tokens):
                op = t if t[0] == '-' else '-' + t
                option = self.find_option(op)
                if option:
                    if considerarg and not m and option.expectsarg:
                        logger.info('option %r expected an arg, taking the rest too', option)
                        # reset the current option if we already took an argument,
                        # this prevents the next word node to also consider itself
                        # as an argument
                        self._currentoption = None
                        return [matchresult(pos, pos+len(chars), option.text, None)]

                    mr = matchresult(pos, pos+len(t), option.text, None)
                    m.append(mr)
                # if the previous option expected an argument and we couldn't
                # match the current token, take the rest as its argument, this
                # covers a series of short options where the last one has an argument
                # with no space between it, such as 'xargs -r0n1'
                elif considerarg and prevoption and prevoption.expectsarg:
                    pmr = m[-1]
                    mr = matchresult(pmr.start, pmr.end+(len(tokens)-i), pmr.text, None)
                    m[-1] = mr
                    # reset the current option if we already took an argument,
                    # this prevents the next word node to also consider itself
                    # as an argument
                    self._currentoption = None
                    break
                else:
                    m.append(self.unknown(t, pos, pos+len(t)))
                pos += len(t)
                prevoption = option
            return m

        if not self.manpage:
            logger.info('inside an unknown command, giving up on %r', word)
            self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
            return

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

            # check if we're inside a nested command and this word marks the end
            if isinstance(self.groupstack[-1][1], list) and word in self.groupstack[-1][1]:
                logger.info('token %r ends current nested command', word)
                self.endcommand()
                mr = matchresult(node.pos[0], node.pos[1], self.matches[-1].text, None)
                self.matches.append(mr)
            elif word != '-' and word.startswith('-') and not word.startswith('--'):
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
                    if self._prevoption.nestedcommand:
                        logger.info('option %r can nest commands', self._prevoption)
                        if self.startcommand([node], self._prevoption.nestedcommand, addunknown=False):
                            self._currentoption = None
                            return

                    pmr = self.matches[-1]
                    mr = matchresult(pmr.start, node.pos[1], pmr.text, None)
                    self.matches[-1] = mr
                else:
                    self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
            else:
                if self.manpage.partialmatch:
                    logger.info('attemping to do a partial match')

                    m = attemptfuzzy(word)
                    if not any(mm.unknown for mm in m):
                        logger.info('found a match for everything, taking it')
                        self.matches.extend(m)
                        return

                if self.manpage.arguments:
                    if self.manpage.nestedcommand:
                        logger.info('manpage %r can nest commands', self.manpage)
                        if self.startcommand([node], self.manpage.nestedcommand, addunknown=False):
                            self._currentoption = None
                            return

                    d = self.manpage.arguments
                    k = list(d.keys())[0]
                    logger.info('got arguments, using %r', k)
                    text = d[k]
                    mr = matchresult(node.pos[0], node.pos[1], text, None)
                    self.matches.append(mr)
                    return

                # if all of that failed, we can't explain it so mark it unknown
                self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))

    def match(self):
        logger.info('matching string %r', self.s)
        self.ast = parser.parse_command_line(self.s)
        self.visit(self.ast)
        assert len(self.groupstack) == 1, 'groupstack should contain only shell group after matching'

        # if we only have one command in there and no shell results, reraise
        # the original exception
        if len(self.groups) == 2 and not self.groups[0].results and self.groups[1].manpage is None:
            raise self.groups[1].error

        def debugmatch():
            s = '\n'.join(['%d) %r = %r' % (i, self.s[m.start:m.end], m.text) for i, m in enumerate(self.allmatches)])
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

        return self.groups

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
