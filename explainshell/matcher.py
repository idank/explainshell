import collections, logging, itertools

import bashlex.parser
import bashlex.ast

from explainshell import errors, util, helpconstants

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

class matcher(bashlex.ast.nodevisitor):
    '''parse a command line and return a list of matchresults describing
    each token.
    '''
    def __init__(self, s, store):
        self.s = s.encode('latin1', 'replace')
        self.store = store
        self._prevoption = self._currentoption = None
        self.groups = [matchgroup('shell')]

        # a list of (start, end, text) tuples where expansions happened
        self.expansions = []

        # a stack to manage nested command groups: whenever a new simple
        # command is started, we push a tuple with:
        # - the node that started this group. this is used to find it when
        #   a command ends (see visitnodeend)
        # - its matchgroup. new matchresults will be added to it.
        # - a word used to end the top-most command. this is used when a flag
        #   starts a new command, e.g.  find -exec.
        self.groupstack = [(None, self.groups[-1], None)]

        # keep a stack of the currently visited compound command (if/for..)
        # to provide context when matching reserved words, since for example
        # the keyword 'done' can appear in a for, while..
        self.compoundstack = []

        # a set of functions defined in the current input, we will try to match
        # commands against them so if one refers to defined function, it won't
        # show up as unknown or be taken from the db
        self.functions = set()

    def _generatecommandgroupname(self):
        existing = len([g for g in self.groups if g.name.startswith('command')])
        return 'command%d' % existing

    @property
    def matches(self):
        '''return the list of results from the most recently created group'''
        return self.groupstack[-1][1].results

    @property
    def allmatches(self):
        return list(itertools.chain.from_iterable(g.results for g in self.groups))

    @property
    def manpage(self):
        group = self.groupstack[-1][1]
        # we do not have a manpage if the top of the stack is the shell group.
        # this can happen if the first argument is a command substitution
        # and we're not treating it as a "man page not found"
        if group.name != 'shell':
            return group.manpage

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

    def visitreservedword(self, node, word):
        # first try the compound reserved words
        helptext = None
        if self.compoundstack:
            currentcompound = self.compoundstack[-1]
            helptext = helpconstants.COMPOUNDRESERVEDWORDS.get(currentcompound, {}).get(word)

        # try these if we don't have anything specific
        if not helptext:
            helptext = helpconstants.RESERVEDWORDS[word]

        self.groups[0].results.append(matchresult(node.pos[0], node.pos[1], helptext, None))

    def visitoperator(self, node, op):
        helptext = None
        if self.compoundstack:
            currentcompound = self.compoundstack[-1]
            helptext = helpconstants.COMPOUNDRESERVEDWORDS.get(currentcompound, {}).get(op)

        if not helptext:
            helptext = helpconstants.OPERATORS[op]

        self.groups[0].results.append(matchresult(node.pos[0], node.pos[1], helptext, None))

    def visitpipe(self, node, pipe):
        self.groups[0].results.append(
                matchresult(node.pos[0], node.pos[1], helpconstants.PIPELINES, None))

    def visitredirect(self, node, input, type, output, heredoc):
        helptext = [helpconstants.REDIRECTION]

        if type == '>&' and isinstance(output, int):
            type = type[:-1]

        if type in helpconstants.REDIRECTION_KIND:
            helptext.append(helpconstants.REDIRECTION_KIND[type])

        self.groups[0].results.append(
                matchresult(node.pos[0], node.pos[1], '\n\n'.join(helptext), None))

        # the output might contain a wordnode, visiting it will confuse the
        # matcher who'll think it's an argument, instead visit the expansions
        # directly, if we have any
        if isinstance(output, bashlex.ast.node):
            for part in output.parts:
                self.visit(part)

        return False

    def visitcommand(self, node, parts):
        assert parts

        # look for the first WordNode, which might not be at parts[0]
        idxwordnode = bashlex.ast.findfirstkind(parts, 'word')
        if idxwordnode == -1:
            logger.info('no words found in command (probably contains only redirects)')
            return

        wordnode = parts[idxwordnode]

        # check if this refers to a previously defined function
        if wordnode.word in self.functions:
            logger.info('word %r is a function, not trying to match it or its '
                        'arguments', wordnode)

            # first, add a matchresult for the function call
            mr = matchresult(wordnode.pos[0], wordnode.pos[1],
                             helpconstants._functioncall % wordnode.word, None)
            self.matches.append(mr)

            # this is a bit nasty: if we were to visit the command like we
            # normally do it would try to match it against a manpage. but
            # we don't have one here, we just want to take all the words and
            # consider them part of the function call
            for part in parts:
                # maybe it's a redirect...
                if part.kind != 'word':
                    self.visit(part)
                else:
                    # this is an argument to the function
                    if part is not wordnode:
                        mr = matchresult(part.pos[0], part.pos[1],
                                         helpconstants._functionarg % wordnode.word,
                                         None)
                        self.matches.append(mr)

                        # visit any expansions in there
                        for ppart in part.parts:
                            self.visit(ppart)

            # we're done with this commandnode, don't visit its children
            return False


        self.startcommand(node, parts, None)

    def visitif(self, *args):
        self.compoundstack.append('if')
    def visitfor(self, node, parts):
        self.compoundstack.append('for')

        for part in parts:
            # don't visit words since they're not part of the current command,
            # instead consider them part of the for construct
            if part.kind == 'word':
                mr = matchresult(part.pos[0], part.pos[1], helpconstants._for, None)
                self.groups[0].results.append(mr)

                # but we do want to visit expanions
                for ppart in part.parts:
                    self.visit(ppart)
            else:
                self.visit(part)

        return False

    def visitwhile(self, *args):
        self.compoundstack.append('while')
    def visituntil(self, *args):
        self.compoundstack.append('until')

    def visitnodeend(self, node):
        if node.kind == 'command':
            # it's possible for visitcommand/end to be called without a command
            # group being pushed if it contains only redirect nodes
            if len(self.groupstack) > 1:
                logger.info('visitnodeend %r, groups %d', node, len(self.groupstack))

                while self.groupstack[-1][0] is not node:
                    logger.info('popping groups that are a result of nested commands')
                    self.endcommand()
                self.endcommand()
        elif node.kind in ('if', 'for', 'while', 'until'):
            kind = self.compoundstack.pop()
            assert kind == node.kind

    def startcommand(self, commandnode, parts, endword, addgroup=True):
        logger.info('startcommand commandnode=%r parts=%r, endword=%r, addgroup=%s',
                    commandnode, parts, endword, addgroup)
        idxwordnode = bashlex.ast.findfirstkind(parts, 'word')
        assert idxwordnode != -1

        wordnode = parts[idxwordnode]
        if wordnode.parts:
            logger.info('node %r has parts (it was expanded), no point in looking'
                        ' up a manpage for it', wordnode)

            if addgroup:
                mg = matchgroup(self._generatecommandgroupname())
                mg.manpage = None
                mg.suggestions = None
                self.groups.append(mg)
                self.groupstack.append((commandnode, mg, endword))

            return False

        startpos, endpos = wordnode.pos

        try:
            mps = self.findmanpages(wordnode.word)
            # we consume this node here, pop it from parts so we
            # don't visit it again as an argument
            parts.pop(idxwordnode)
        except errors.ProgramDoesNotExist, e:
            if addgroup:
                # add a group for this command, we'll mark it as unknown
                # when visitword is called
                logger.info('no manpage found for %r, adding a group for it',
                            wordnode.word)

                mg = matchgroup(self._generatecommandgroupname())
                mg.error = e
                mg.manpage = None
                mg.suggestions = None
                self.groups.append(mg)
                self.groupstack.append((commandnode, mg, endword))

            return False

        manpage = mps[0]
        idxnextwordnode = bashlex.ast.findfirstkind(parts, 'word')

        # check the next word for a possible multicommand if:
        # - the matched manpage says so
        # - we have another word node
        # - the word node has no expansions in it
        if manpage.multicommand and idxnextwordnode != -1 and not parts[idxnextwordnode].parts:
            nextwordnode = parts[idxnextwordnode]
            try:
                multi = '%s %s' % (wordnode.word, nextwordnode.word)
                logger.info('%r is a multicommand, trying to get another token and look up %r', manpage, multi)
                mps = self.findmanpages(multi)
                manpage = mps[0]
                # we consume this node here, pop it from parts so we
                # don't visit it again as an argument
                parts.pop(idxnextwordnode)
                endpos = nextwordnode.pos[1]
            except errors.ProgramDoesNotExist:
                logger.info('no manpage %r for multicommand %r', multi, manpage)

        # create a new matchgroup for the current command
        mg = matchgroup(self._generatecommandgroupname())
        mg.manpage = manpage
        mg.suggestions = mps[1:]
        self.groups.append(mg)
        self.groupstack.append((commandnode, mg, endword))

        self.matches.append(matchresult(startpos, endpos, manpage.synopsis, None))
        return True

    def endcommand(self):
        '''end the most recently created command group by popping it from the
        group stack. groups are created by visitcommand or a nested command'''
        assert len(self.groupstack) >= 2, 'groupstack must contain shell and command groups'
        g = self.groupstack.pop()
        logger.info('ending group %s', g)

    def visitcommandsubstitution(self, node, command):
        kind = self.s[node.pos[0]]
        substart = 2 if kind == '$' else 1

        helptext = None
        # start the expansion after the $( or `
        self.expansions.append((node.pos[0] + substart,
                                node.pos[1] - 1, helptext))

        # do not try to match the child nodes
        return False

    def visitprocesssubstitution(self, node, command):
        # don't include opening <( and closing )
        self.expansions.append((node.pos[0] + 2,
                                node.pos[1] - 1, None))

        # do not try to match the child nodes
        return False

    def visitassignment(self, node, word):
        helptext = helpconstants.ASSIGNMENT
        self.groups[0].results.append(matchresult(node.pos[0], node.pos[1], helptext, None))

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

        def _visitword(node, word):
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

                # check if we splitted the word just above, if we did then reset
                # the current option so the next word doesn't consider itself
                # an argument
                if word != node.word:
                    self._currentoption = None
            else:
                word = node.word

                # check if we're inside a nested command and this word marks the end
                if isinstance(self.groupstack[-1][-1], list) and word in self.groupstack[-1][-1]:
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
                            if self.startcommand(None, [node], self._prevoption.nestedcommand, addgroup=False):
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
                            if self.startcommand(None, [node], self.manpage.nestedcommand, addgroup=False):
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

        _visitword(node, word)

    def visitfunction(self, node, name, body, parts):
        self.functions.add(name.word)

        def _iscompoundopenclosecurly(compound):
            first, last = compound.list[0], compound.list[-1]
            if (first.kind == 'reservedword' and last.kind == 'reservedword' and
                first.word == '{' and last.word == '}'):
                return True

        # if the compound command we have there is { }, let's include the
        # {} as part of the function declaration. normally it would be
        # treated as a group command, but that seems less informative in this
        # context
        if _iscompoundopenclosecurly(body):
            # create a matchresult until after the first {
            mr = matchresult(node.pos[0], body.list[0].pos[1],
                             helpconstants._function, None)
            self.groups[0].results.append(mr)

            # create a matchresult for the closing }
            mr = matchresult(body.list[-1].pos[0], body.list[-1].pos[1],
                             helpconstants._function, None)
            self.groups[0].results.append(mr)

            # visit anything in between the { }
            for part in body.list[1:-1]:
                self.visit(part)
        else:
            beforebody = bashlex.ast.findfirstkind(parts, 'compound') - 1
            assert beforebody > 0
            beforebody = parts[beforebody]

            # create a matchresult ending at the node before body
            mr = matchresult(node.pos[0], beforebody.pos[1],
                             helpconstants._function, None)
            self.groups[0].results.append(mr)

            self.visit(body)

        return False

    def match(self):
        logger.info('matching string %r', self.s)

        # limit recursive parsing to a depth of 1
        self.ast = bashlex.parser.parsesingle(self.s, expansionlimit=1)
        self.visit(self.ast)
        assert len(self.groupstack) == 1, 'groupstack should contain only shell group after matching'

        # if we only have one command in there and no shell results/expansions,
        # reraise the original exception
        if (len(self.groups) == 2 and not self.groups[0].results and
            self.groups[1].manpage is None and not self.expansions):
            raise self.groups[1].error

        def debugmatch():
            s = '\n'.join(['%d) %r = %r' % (i, self.s[m.start:m.end], m.text) for i, m in enumerate(self.allmatches)])
            return s

        self._markunparsedunknown()

        # fix each matchgroup seperately
        for group in self.groups:
            if group.results:
                group.results = self._mergeadjacent(group.results)

                # add matchresult.match to existing matches
                for i, m in enumerate(group.results):
                    assert m.end <= len(self.s), '%d %d' % (m.end, len(self.s))

                    portion = self.s[m.start:m.end].decode('latin1')
                    group.results[i] = matchresult(m.start, m.end, m.text, portion)

        logger.debug('%r matches:\n%s', self.s, debugmatch())

        # not strictly needed, but doesn't hurt
        self.expansions.sort()

        return self.groups

    def _markunparsedunknown(self):
        '''the parser may leave a remainder at the end of the string if it doesn't
        match any of the rules, mark them as unknowns'''
        parsed = [False]*len(self.s)

        # go over all existing matches to see if we've covered the
        # current position
        for start, end, _, _ in self.allmatches:
            for i in range(start, end):
                parsed[i] = True

        for i in range(len(parsed)):
            # whitespace is always 'unparsed'
            if self.s[i].isspace():
                parsed[i] = True

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
