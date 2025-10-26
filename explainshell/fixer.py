import textwrap
import logging

from explainshell import util


class BaseFixer:
    """The base fixer class which other fixers inherit from.

    Subclasses override the base methods in order to fix manpage content during
    different parts of the parsing/classifying/saving process."""

    run_before = []
    run_last = False

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


fixers_cls = []
fixerspriority = {}


class Runner:
    """The runner coordinates the fixers."""

    def __init__(self, mctx):
        self.mctx = mctx
        self.fixers = [f(mctx) for f in fixers_cls]

    def disable(self, name):
        before = len(self.fixers)
        self.fixers = [f for f in self.fixers if f.__class__.__name__ != name]
        if before == len(self.fixers):
            raise ValueError(f"fixer {name} not found")

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


def register(fixer_cls):
    fixers_cls.append(fixer_cls)
    for f in fixer_cls.run_before:
        if not hasattr(f, "_parents"):
            f._parents = []
        f._parents.append(fixer_cls)
    return fixer_cls


@register
class BulletRemover(BaseFixer):
    """remove list bullets from paragraph start, see mysqlslap.1"""

    def post_parse_manpage(self):
        to_remove = []
        for i, p in enumerate(self.mctx.manpage.paragraphs):
            try:
                idx = p.text.index("\xc2\xb7")
                p.text = p.text[:idx] + p.text[idx + 2 :]
                if not p.text.strip():
                    to_remove.append(i)
            except ValueError:
                pass
        for i in reversed(to_remove):
            del self.mctx.manpage.paragraphs[i]


@register
class LeadingSpaceRemover(BaseFixer):
    """go over all known option paragraphs and remove their leading spaces
    by the amount of spaces in the first line"""

    def post_option_extraction(self):
        for i, p in enumerate(self.mctx.manpage.options):
            text = self._remove_ws(p.text)
            p.text = text

    def _remove_ws(self, text):
        """
        >>> f = LeadingSpaceRemover(None)
        >>> f._remove_ws(' a\\n  b ')
        'a\\n b'
        >>> f._remove_ws('\\t a\\n\\t \\tb')
        'a\\n\\tb'
        """
        return textwrap.dedent(text).rstrip()


@register
class TarFixer(BaseFixer):
    def __init__(self, *args):
        super().__init__(*args)
        self.run = self.mctx.name == "tar"

    def pre_add_manpage(self):
        self.mctx.manpage.partial_match = True


@register
class ParagraphJoiner(BaseFixer):
    run_before = [LeadingSpaceRemover]
    max_distance = 5

    def post_option_extraction(self):
        options = [p for p in self.mctx.manpage.paragraphs if p.is_option]
        self._join(self.mctx.manpage.paragraphs, options)

    def _join(self, paragraphs, options):
        def _paragraphs_between(op1, op2):
            assert op1.idx < op2.idx
            r = []
            start = None
            for i, p in enumerate(paragraphs):
                if op1.idx < p.idx < op2.idx:
                    if not r:
                        start = i
                    r.append(p)
            return r, start

        total_merged = 0
        for curr, o_next in util.pairwise(options):
            between, start = _paragraphs_between(curr, o_next)
            if curr.section == o_next.section and 1 <= len(between) < self.max_distance:
                self.logger.info(
                    "merging paragraphs %d through %d (inclusive)",
                    curr.idx,
                    o_next.idx - 1,
                )
                new_desc = [curr.text.rstrip()]
                new_desc.extend([p.text.rstrip() for p in between])
                curr.text = "\n\n".join(new_desc)
                del paragraphs[start: start + len(between)]
                total_merged += len(between)
        return total_merged


@register
class OptionTrimmer(BaseFixer):
    run_before = [ParagraphJoiner]

    d = {"git-rebase": (50, -1)}

    def __init__(self, mctx):
        super().__init__(mctx)
        self.run = self.mctx.name in self.d

    def post_classify(self):
        start, end = self.d[self.mctx.name]
        classified_opts = [p for p in self.mctx.manpage.paragraphs if p.is_option]
        assert classified_opts
        if end == -1:
            end = classified_opts[-1].idx
        else:
            assert start > end

        for p in classified_opts:
            if not start <= p.idx <= end:
                p.is_option = False
                self.logger.info("removing option %r", p)


def _parents(fixer_cls):
    p = getattr(fixer_cls, "_parents", [])
    last = fixer_cls.run_last

    if last and p:
        raise ValueError(
            f"{fixer_cls.__name__} can't be last and also run before someone else"
        )

    if last:
        return [f for f in fixers_cls if f is not fixer_cls]
    return p


fixers_cls = util.topo_sorted(fixers_cls, _parents)
