import collections
import logging
import itertools

import bashlex.parser
import bashlex.ast

from explainshell import errors, help_constants, util


class MatchGroup:
    """a class to group matchresults together

    we group all shell results in one group and create a new group for every
    command"""

    def __init__(self, name):
        self.name = name
        self.results = []

    def __repr__(self):
        return "<matchgroup %r with %d results>" % (self.name, len(self.results))


class MatchResult(collections.namedtuple("MatchResult", "start end text match")):
    @property
    def unknown(self):
        return self.text is None


match_word_exp = collections.namedtuple("match_word_exp", "start end kind")

logger = logging.getLogger(__name__)


class Matcher(bashlex.ast.nodevisitor):
    """parse a command line and return a list of `MatchResult`s describing
    each token.
    """

    def __init__(self, s, store):
        self.s = s.encode("latin1", "replace")
        self.store = store
        self._prev_option = self._current_option = None
        self.groups = [MatchGroup("shell")]

        # a list of matchwordexpansions where expansions happened during word
        # expansion
        self.expansions = []

        # a stack to manage nested command groups: whenever a new simple
        # command is started, we push a tuple with:
        # - the node that started this group. this is used to find it when
        #   a command ends (see visitnodeend)
        # - its `MatchGroup`. new `MatchResult`s will be added to it.
        # - a word used to end the top-most command. this is used when a flag
        #   starts a new command, e.g.  find -exec.
        self.group_stack = [(None, self.groups[-1], None)]

        # keep a stack of the currently visited compound command (if/for..)
        # to provide context when matching reserved words, since for example
        # the keyword 'done' can appear in a for, while..
        self.compound_stack = []

        # a set of functions defined in the current input, we will try to match
        # commands against them so if one refers to defined function, it won't
        # show up as unknown or be taken from the db
        self.functions = set()

    def _generate_cmd_group_name(self):
        existing = len([g for g in self.groups if g.name.startswith("command")])
        return f"command{existing}"

    @property
    def matches(self):
        """return the list of results from the most recently created group"""
        return self.group_stack[-1][1].results

    @property
    def all_matches(self):
        return list(itertools.chain.from_iterable(g.results for g in self.groups))

    @property
    def man_page(self):
        group = self.group_stack[-1][1]
        # we do not have a manpage if the top of the stack is the shell group.
        # this can happen if the first argument is a command substitution
        # and we're not treating it as a "man page not found"
        if group.name != "shell":
            return group.manpage

    def find_option(self, opt):
        self._current_option = self.man_page.find_option(opt)
        logger.debug("looking up option %r, got %r", opt, self._current_option)
        return self._current_option

    def find_man_pages(self, prog):
        logger.info("looking up %r in store", prog)
        man_pages = self.store.find_man_page(prog)
        logger.info("found %r in store, got: %r, using %r", prog, man_pages, man_pages[0])
        return man_pages

    def unknown(self, token, start, end):
        logger.debug("nothing to do with token %r", token)
        return MatchResult(start, end, None, None)

    def visitreservedword(self, node, word):
        # first try the compound reserved words
        helptext = None
        if self.compound_stack:
            current_compound = self.compound_stack[-1]
            helptext = help_constants.COMPOUND_RESERVED_WORDS.get(current_compound, {}).get(
                word
            )

        # try these if we don't have anything specific
        if not helptext:
            helptext = help_constants.RESERVED_WORDS[word]

        self.groups[0].results.append(
            MatchResult(node.pos[0], node.pos[1], helptext, None)
        )

    def visitoperator(self, node, op):
        helptext = None
        if self.compound_stack:
            curr_compound = self.compound_stack[-1]
            helptext = help_constants.COMPOUND_RESERVED_WORDS.get(curr_compound, {}).get(
                op
            )

        if not helptext:
            helptext = help_constants.OPERATORS[op]

        self.groups[0].results.append(
            MatchResult(node.pos[0], node.pos[1], helptext, None)
        )

    def visitpipe(self, node, pipe):
        self.groups[0].results.append(
            MatchResult(node.pos[0], node.pos[1], help_constants.PIPELINES, None)
        )

    def visitredirect(self, node, input, r_type, output, heredoc):
        helptext = [help_constants.REDIRECTION]

        if r_type == ">&" and isinstance(output, int):
            r_type = r_type[:-1]

        if r_type in help_constants.REDIRECTION_KIND:
            helptext.append(help_constants.REDIRECTION_KIND[r_type])

        self.groups[0].results.append(
            MatchResult(node.pos[0], node.pos[1], "\n\n".join(helptext), None)
        )

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
        idx_word_node = bashlex.ast.findfirstkind(parts, "word")
        if idx_word_node == -1:
            logger.info("no words found in command (probably contains only redirects)")
            return

        word_node = parts[idx_word_node]

        # check if this refers to a previously defined function
        if word_node.word in self.functions:
            logger.info(
                f"word {word_node} is a function, not trying to match it or it's arguments"
            )

            # first, add a MatchResult for the function call
            mr = MatchResult(
                word_node.pos[0],
                word_node.pos[1],
                help_constants._function_call % word_node.word,
                None,
            )
            self.matches.append(mr)

            # this is a bit nasty: if we were to visit the command like we
            # normally do it would try to match it against a manpage. but
            # we don't have one here, we just want to take all the words and
            # consider them part of the function call
            for part in parts:
                # maybe it's a redirect...
                if part.kind != "word":
                    self.visit(part)
                else:
                    # this is an argument to the function
                    if part is not word_node:
                        mr = MatchResult(
                            part.pos[0],
                            part.pos[1],
                            help_constants._functionarg % word_node.word,
                            None,
                        )
                        self.matches.append(mr)

                        # visit any expansions in there
                        for p_part in part.parts:
                            self.visit(p_part)

            # we're done with this commandnode, don't visit its children
            return False

        self.startcommand(node, parts, None)

    def visitif(self, *args):
        self.compound_stack.append("if")

    def visitfor(self, node, parts):
        self.compound_stack.append("for")

        for part in parts:
            # don't visit words since they're not part of the current command,
            # instead consider them part of the for construct
            if part.kind == "word":
                mr = MatchResult(part.pos[0], part.pos[1], help_constants._for, None)
                self.groups[0].results.append(mr)

                # but we do want to visit expansions
                for p_part in part.parts:
                    self.visit(p_part)
            else:
                self.visit(part)

        return False

    def visitwhile(self, *args):
        self.compound_stack.append("while")

    def visituntil(self, *args):
        self.compound_stack.append("until")

    def visitnodeend(self, node):
        if node.kind == "command":
            # it's possible for visitcommand/end to be called without a command
            # group being pushed if it contains only redirect nodes
            if len(self.group_stack) > 1:
                logger.info("visitnodeend %r, groups %d", node, len(self.group_stack))

                while self.group_stack[-1][0] is not node:
                    logger.info("popping groups that are a result of nested commands")
                    self.endcommand()
                self.endcommand()
        elif node.kind in ("if", "for", "while", "until"):
            kind = self.compound_stack.pop()
            assert kind == node.kind

    def startcommand(self, commandnode, parts, endword, addgroup=True):
        logger.info(
            "startcommand commandnode=%r parts=%r, endword=%r, addgroup=%s",
            commandnode,
            parts,
            endword,
            addgroup,
        )
        idx_word_node = bashlex.ast.findfirstkind(parts, "word")
        assert idx_word_node != -1

        word_node = parts[idx_word_node]
        if word_node.parts:
            logger.info(
                "node %r has parts (it was expanded), no point in looking"
                " up a manpage for it",
                word_node,
            )

            if addgroup:
                mg = MatchGroup(self._generate_cmd_group_name())
                mg.manpage = None
                mg.suggestions = None
                self.groups.append(mg)
                self.group_stack.append((commandnode, mg, endword))

            return False

        startpos, endpos = word_node.pos

        try:
            mps = self.find_man_pages(word_node.word)
            # we consume this node here, pop it from parts so we
            # don't visit it again as an argument
            parts.pop(idx_word_node)
        except errors.ProgramDoesNotExist as error_msg:
            if addgroup:
                # add a group for this command, we'll mark it as unknown
                # when visitword is called
                logger.info(
                    f"no manpage found for {word_node.word}, adding a group for it"
                )

                mg = MatchGroup(self._generate_cmd_group_name())
                mg.error = error_msg
                mg.manpage = None
                mg.suggestions = None
                self.groups.append(mg)
                self.group_stack.append((commandnode, mg, endword))

            return False

        manpage = mps[0]
        idx_next_word_node = bashlex.ast.findfirstkind(parts, "word")

        # check the next word for a possible multi_cmd if:
        # - the matched manpage says so
        # - we have another word node
        # - the word node has no expansions in it
        if (
            manpage.multi_cmd
            and idx_next_word_node != -1
            and not parts[idx_next_word_node].parts
        ):
            next_word_node = parts[idx_next_word_node]
            try:
                multi = f"{word_node.word} {next_word_node.word}"
                logger.info(
                    f"{manpage} is a multi_cmd, trying to get another token and look up {multi}"
                )
                mps = self.find_man_pages(multi)
                manpage = mps[0]
                # we consume this node here, pop it from parts so we
                # don't visit it again as an argument
                parts.pop(idx_next_word_node)
                endpos = next_word_node.pos[1]
            except errors.ProgramDoesNotExist:
                logger.info("no manpage %r for multi_cmd %r", multi, manpage)

        # create a new MatchGroup for the current command
        mg = MatchGroup(self._generate_cmd_group_name())
        mg.manpage = manpage
        mg.suggestions = mps[1:]
        self.groups.append(mg)
        self.group_stack.append((commandnode, mg, endword))

        self.matches.append(
            MatchResult(
                startpos, endpos, manpage.synopsis or help_constants.NO_SYNOPSIS, None
            )
        )
        return True

    def endcommand(self):
        """end the most recently created command group by popping it from the
        group stack. groups are created by visitcommand or a nested command"""
        assert (
            len(self.group_stack) >= 2
        ), "groupstack must contain shell and command groups"
        g = self.group_stack.pop()
        logger.info("ending group %s", g)

    def visitcommandsubstitution(self, node, command):
        kind = self.s[node.pos[0]]
        sub_start = 2 if kind == "$" else 1

        # start the expansion after the $( or `
        self.expansions.append(
            match_word_exp(node.pos[0] + sub_start, node.pos[1] - 1, "substitution")
        )

        # do not try to match the child nodes
        return False

    def visitprocesssubstitution(self, node, command):
        # don't include opening <( and closing )
        self.expansions.append(
            match_word_exp(node.pos[0] + 2, node.pos[1] - 1, "substitution")
        )

        # do not try to match the child nodes
        return False

    def visitassignment(self, node, word):
        helptext = help_constants.ASSIGNMENT
        self.groups[0].results.append(
            MatchResult(node.pos[0], node.pos[1], helptext, None)
        )

    def visitword(self, node, word):
        def attemptfuzzy(chars):
            m = []
            if chars[0] == "-":
                tokens = [chars[0:2]] + list(chars[2:])
                considerarg = True
            else:
                tokens = list(chars)
                considerarg = False

            pos = node.pos[0]
            prevoption = None
            for i, t in enumerate(tokens):
                op = t if t[0] == "-" else "-" + t
                option = self.find_option(op)
                if option:
                    if considerarg and not m and option.expects_arg:
                        logger.info(
                            "option %r expected an arg, taking the rest too", option
                        )
                        # reset the current option if we already took an argument,
                        # this prevents the next word node to also consider itself
                        # as an argument
                        self._current_option = None
                        return [MatchResult(pos, pos + len(chars), option.text, None)]

                    mr = MatchResult(pos, pos + len(t), option.text, None)
                    m.append(mr)
                # if the previous option expected an argument and we couldn't
                # match the current token, take the rest as its argument, this
                # covers a series of short options where the last one has an argument
                # with no space between it, such as 'xargs -r0n1'
                elif considerarg and prevoption and prevoption.expects_arg:
                    pmr = m[-1]
                    mr = MatchResult(
                        pmr.start, pmr.end + (len(tokens) - i), pmr.text, None
                    )
                    m[-1] = mr
                    # reset the current option if we already took an argument,
                    # this prevents the next word node to also consider itself
                    # as an argument
                    self._current_option = None
                    break
                else:
                    m.append(self.unknown(t, pos, pos + len(t)))
                pos += len(t)
                prevoption = option
            return m

        def _visitword(node, word):
            if not self.man_page:
                logger.info("inside an unknown command, giving up on %r", word)
                self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))
                return

            logger.info("trying to match token: %r", word)

            self._prev_option = self._current_option
            if word.startswith("--"):
                word = word.split("=", 1)[0]
            option = self.find_option(word)
            if option:
                logger.info("found an exact match for %r: %r", word, option)
                mr = MatchResult(node.pos[0], node.pos[1], option.text, None)
                self.matches.append(mr)

                # check if we splitted the word just above, if we did then reset
                # the current option so the next word doesn't consider itself
                # an argument
                if word != node.word:
                    self._current_option = None
            else:
                word = node.word

                # check if we're inside a nested command and this word marks the end
                if (
                    isinstance(self.group_stack[-1][-1], list)
                    and word in self.group_stack[-1][-1]
                ):
                    logger.info("token %r ends current nested command", word)
                    self.endcommand()
                    mr = MatchResult(
                        node.pos[0], node.pos[1], self.matches[-1].text, None
                    )
                    self.matches.append(mr)
                elif word != "-" and word.startswith("-") and not word.startswith("--"):
                    logger.debug("looks like a short option")
                    if len(word) > 2:
                        logger.info("trying to split it up")
                        self.matches.extend(attemptfuzzy(word))
                    else:
                        self.matches.append(
                            self.unknown(word, node.pos[0], node.pos[1])
                        )
                elif self._prev_option and self._prev_option.expects_arg:
                    logger.info(
                        "previous option possibly expected an arg, and we can't"
                        " find an option to match the current token, assuming it's an arg"
                    )
                    ea = self._prev_option.expects_arg
                    possible_args = ea if isinstance(ea, list) else []
                    take = True
                    if possible_args and word not in possible_args:
                        take = False
                        logger.info(
                            "token %r not in list of possible args %r for %r",
                            word,
                            possible_args,
                            self._prev_option,
                        )
                    if take:
                        if self._prev_option.nested_cmd:
                            logger.info("option %r can nest commands", self._prev_option)
                            if self.startcommand(
                                None,
                                [node],
                                self._prev_option.nested_cmd,
                                addgroup=False,
                            ):
                                self._current_option = None
                                return

                        pmr = self.matches[-1]
                        mr = MatchResult(pmr.start, node.pos[1], pmr.text, None)
                        self.matches[-1] = mr
                    else:
                        self.matches.append(
                            self.unknown(word, node.pos[0], node.pos[1])
                        )
                else:
                    if self.man_page.partial_match:
                        logger.info("attempting to do a partial match")

                        m = attemptfuzzy(word)
                        if not any(mm.unknown for mm in m):
                            logger.info("found a match for everything, taking it")
                            self.matches.extend(m)
                            return

                    if self.man_page.arguments:
                        if self.man_page.nested_cmd:
                            logger.info("manpage %r can nest commands", self.man_page)
                            if self.startcommand(
                                None, [node], self.man_page.nested_cmd, addgroup=False
                            ):
                                self._current_option = None
                                return

                        d = self.man_page.arguments
                        k = list(d.keys())[0]
                        logger.info("got arguments, using %r", k)
                        text = d[k]
                        mr = MatchResult(node.pos[0], node.pos[1], text, None)
                        self.matches.append(mr)
                        return

                    # if all of that failed, we can't explain it so mark it unknown
                    self.matches.append(self.unknown(word, node.pos[0], node.pos[1]))

        _visitword(node, word)

    def visitfunction(self, node, name, body, parts):
        self.functions.add(name.word)

        def _iscompoundopenclosecurly(compound):
            first, last = compound.list[0], compound.list[-1]
            if (
                first.kind == "reservedword"
                and last.kind == "reservedword"
                and first.word == "{"
                and last.word == "}"
            ):
                return True

        # if the compound command we have there is { }, let's include the
        # {} as part of the function declaration. normally it would be
        # treated as a group command, but that seems less informative in this
        # context
        if _iscompoundopenclosecurly(body):
            # create a matchresult until after the first {
            mr = MatchResult(
                node.pos[0], body.list[0].pos[1], help_constants._function, None
            )
            self.groups[0].results.append(mr)

            # create a matchresult for the closing }
            mr = MatchResult(
                body.list[-1].pos[0],
                body.list[-1].pos[1],
                help_constants._function,
                None,
            )
            self.groups[0].results.append(mr)

            # visit anything in between the { }
            for part in body.list[1:-1]:
                self.visit(part)
        else:
            beforebody = bashlex.ast.findfirstkind(parts, "compound") - 1
            assert beforebody > 0
            beforebody = parts[beforebody]

            # create a matchresult ending at the node before body
            mr = MatchResult(
                node.pos[0], beforebody.pos[1], help_constants._function, None
            )
            self.groups[0].results.append(mr)

            self.visit(body)

        return False

    def visittilde(self, node, value):
        self.expansions.append(match_word_exp(node.pos[0], node.pos[1], "tilde"))

    def visitparameter(self, node, value):
        try:
            int(value)
            kind = "digits"
        except ValueError:
            kind = help_constants.parameters.get(value, "param")

        self.expansions.append(
            match_word_exp(node.pos[0], node.pos[1], f"parameter-{kind}")
        )

    def match(self):
        if isinstance(self.s, bytes):
            self.s = self.s.decode("utf-8")
        logger.info(f"matching string {self.s}")

        # limit recursive parsing to a depth of 1
        self.ast = bashlex.parser.parsesingle(
            self.s, expansionlimit=1, strictmode=False
        )
        if self.ast:
            self.visit(self.ast)
            assert (
                len(self.group_stack) == 1
            ), "groupstack should contain only shell group after matching"

            # if we only have one command in there and no shell results/expansions,
            # reraise the original exception
            if (
                len(self.groups) == 2
                and not self.groups[0].results
                and self.groups[1].manpage is None
                and not self.expansions
            ):
                raise self.groups[1].error
        else:
            logger.warning("no AST generated for %r", self.s)

        def debug_match():
            s = "\n".join(
                [
                    f"{i}) {self.s[m.start: m.end]} = {m.text}"
                    for i, m in enumerate(self.all_matches)
                ]
            )
            return s

        self._mark_unparsed_unknown()

        # fix each MatchGroup separately
        for group in self.groups:
            if group.results:
                if getattr(group, "manpage", None):
                    # ensure that the program part isn't unknown (i.e. it has
                    # something as its synopsis)
                    assert not group.results[0].unknown

                group.results = self._merge_adjacent(group.results)

                # add MatchResult.match to existing matches
                for i, m in enumerate(group.results):
                    assert m.end <= len(self.s), f"{m.end} {len(self.s)}"

                    portion = self.s[m.start: m.end]
                    group.results[i] = MatchResult(m.start, m.end, m.text, portion)

        logger.debug("%r matches:\n%s", self.s, debug_match())

        # not strictly needed, but doesn't hurt
        self.expansions.sort()

        return self.groups

    def _mark_unparsed_unknown(self):
        """the parser may leave a remainder at the end of the string if it doesn't
        match any of the rules, mark them as unknowns"""
        parsed = [False] * len(self.s)

        # go over all existing matches to see if we've covered the
        # current position
        for start, end, _, _ in self.all_matches:
            for i in range(start, end):
                parsed[i] = True

        for i, parsed_i in enumerate(parsed):
            c = self.s[i]
            # whitespace is always 'unparsed'
            if c.isspace():
                parsed[i] = True

            # the parser ignores comments but we can use a trick to see if this
            # starts a comment and is beyond the ending index of the parsed
            # portion of the input
            if (not self.ast or i > self.ast.pos[1]) and c == "#":
                comment = MatchResult(i, len(parsed), help_constants.COMMENT, None)
                self.groups[0].results.append(comment)
                break

            if not parsed[i]:
                # add unparsed results to the 'shell' group
                self.groups[0].results.append(self.unknown(c, i, i + 1))

        # there are no overlaps, so sorting by the start is enough
        self.groups[0].results.sort(key=lambda mr: mr.start)

    def _result_index(self):
        """return a mapping of `MatchResult`s to their index among all
        matches, sorted by the start position of the `MatchResult`"""
        d = {}
        i = 0
        for result in sorted(self.all_matches, key=lambda mr: mr.start):
            d[result] = i
            i += 1
        return d

    def _merge_adjacent(self, matches):
        merged = []
        result_index = self._result_index()
        same_text = itertools.groupby(matches, lambda m: m.text)
        for text, ll in same_text:
            for l_group in util.group_continuous(ll, key=lambda m: result_index[m]):
                l_group = list(l_group)

                if len(l_group) == 1:
                    merged.append(l_group[0])
                else:
                    start = l_group[0].start
                    end = l_group[-1].end
                    end_index = result_index[l_group[-1]]
                    for mr in l_group:
                        del result_index[mr]
                    merged.append(MatchResult(start, end, text, None))
                    result_index[merged[-1]] = end_index
        return merged
