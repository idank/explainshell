"""
data objects to save processed man pages to mongodb
"""

import collections
import re
import logging

import pymongo

from explainshell import errors, help_constants, util, config

logger = logging.getLogger(__name__)


class ClassifierManpage(collections.namedtuple("ClassifierManpage", "name paragraphs")):
    """a man page that had its paragraphs manually tagged as containing options
    or not"""

    @staticmethod
    def from_store(d):
        m = ClassifierManpage(
            d["name"], [Paragraph.from_store(p) for p in d["paragraphs"]]
        )
        return m

    def to_store(self):
        return {
            "name": self.name,
            "paragraphs": [p.to_store() for p in self.paragraphs],
        }


class Paragraph:
    """a paragraph inside a man page is text that ends with two new lines"""

    def __init__(self, idx, text, section, is_option):
        self.idx = idx
        self.text = text
        self.section = section
        self.is_option = is_option

    def clean_text(self):
        t = re.sub(r"<[^>]+>", "", self.text)
        t = re.sub("&lt;", "<", t)
        t = re.sub("&gt;", ">", t)
        return t

    @staticmethod
    def from_store(d):
        p = Paragraph(
            d.get("idx", 0), d["text"].encode("utf8"), d["section"], d["is_option"]
        )
        return p

    def to_store(self):
        return {
            "idx": self.idx,
            "text": self.text,
            "section": self.section,
            "is_option": self.is_option,
        }

    def __repr__(self):
        t = self.clean_text()
        t = t[: min(20, t.find("\n"))].lstrip()
        return "<paragraph %d, %s: %r>" % (self.idx, self.section, t)

    def __eq__(self, other):
        if not other:
            return False
        return self.__dict__ == other.__dict__


class Option(Paragraph):
    """a paragraph that contains extracted options

    short - a list of short options (-a, -b, ..)
    long - a list of long options (--a, --b)
    expects_arg - specifies if one of the short/long options expects an additional argument
    argument - specifies if to consider this as positional arguments
    nested_cmd - specifies if the arguments to this option can start a nested command
    """

    def __init__(self, p, short, long, expects_arg, argument=None, nested_cmd=False):
        Paragraph.__init__(self, p.idx, p.text, p.section, p.is_option)
        self.short = short
        self.long = long
        self._opts = self.short + self.long
        self.argument = argument
        self.expects_arg = expects_arg
        self.nested_cmd = nested_cmd
        if nested_cmd:
            assert (
                expects_arg
            ), "an option that can nest commands must expect an argument"

    @property
    def opts(self):
        return self._opts

    @classmethod
    def from_store(cls, d):
        p = Paragraph.from_store(d)

        return cls(
            p,
            d["short"],
            d["long"],
            d["expects_arg"],
            d["argument"],
            d.get("nested_cmd"),
        )

    def to_store(self):
        d = Paragraph.to_store(self)
        assert d["is_option"]
        d["short"] = self.short
        d["long"] = self.long
        d["expects_arg"] = self.expects_arg
        d["argument"] = self.argument
        d["nested_cmd"] = self.nested_cmd
        return d

    def __str__(self):
        return "(%s)" % ", ".join([str(x) for x in self.opts])

    def __repr__(self):
        return f"<options for paragraph {self.idx}: {self}"


class ManPage:
    """processed man page

    source - the path to the original source man page
    name - the name of this man page as extracted by manpage.manpage
    synopsis - the synopsis of this man page as extracted by manpage.manpage
    paragraphs - a list of paragraphs (and options) that contain all of the text and options
        extracted from this man page
    aliases - a list of aliases found for this man page
    partial_match - allow interpreting options without a leading '-'
    multi_cmd - consider sub commands when explaining a command with this man page,
        e.g. git -> git commit
    updated - whether this man page was manually updated
    nested_cmd - specifies if positional arguments to this program can start a nested command,
        e.g. sudo, xargs
    """

    def __init__(
        self,
        source,
        name,
        synopsis,
        paragraphs,
        aliases,
        partial_match=False,
        multi_cmd=False,
        updated=False,
        nested_cmd=False,
    ):
        self.source = source
        self.name = name
        self.synopsis = synopsis
        self.paragraphs = paragraphs
        self.aliases = aliases
        self.partial_match = partial_match
        self.multi_cmd = multi_cmd
        self.updated = updated
        self.nested_cmd = nested_cmd

    def remove_option(self, idx):
        for i, p in self.paragraphs:
            if p.idx == idx:
                if not isinstance(p, Option):
                    raise ValueError(f"paragraph {idx} isn't an option")
                self.paragraphs[i] = Paragraph(p.idx, p.text, p.section, False)
                return
        raise ValueError(f"idx {idx} not found")

    @property
    def name_section(self):
        name, section = util.name_section(self.source[:-3])
        return f"{name}({section})"

    @property
    def section(self):
        name, section = util.name_section(self.source[:-3])
        return section

    @property
    def options(self):
        return [p for p in self.paragraphs if isinstance(p, Option)]

    @property
    def arguments(self):
        # go over all paragraphs and look for those with the same 'argument'
        # field
        groups = collections.OrderedDict()
        for opt in self.options:
            if opt.argument:
                groups.setdefault(opt.argument, []).append(opt)

        # merge all the paragraphs under the same argument to a single string
        for k, ln in groups.items():
            groups[k] = "\n\n".join([p.text for p in ln])

        return groups

    @property
    def synopsis_no_name(self):
        return re.match(r"[\w|-]+ - (.*)$", self.synopsis).group(1)

    def find_option(self, flag):
        for o_tmp in self.options:
            for o in o_tmp.opts:
                if o == flag:
                    return o_tmp

    def to_store(self):
        return {
            "source": self.source,
            "name": self.name,
            "synopsis": self.synopsis,
            "paragraphs": [p.to_store() for p in self.paragraphs],
            "aliases": self.aliases,
            "partial_match": self.partial_match,
            "multi_cmd": self.multi_cmd,
            "updated": self.updated,
            "nested_cmd": self.nested_cmd,
        }

    @staticmethod
    def from_store(d):
        paragraphs = []
        for pd in d.get("paragraphs", []):
            pp = Paragraph.from_store(pd)
            if pp.is_option is True and "short" in pd:
                pp = Option.from_store(pd)
            paragraphs.append(pp)

        synopsis = d["synopsis"]
        if synopsis:
            synopsis = synopsis.encode("utf8")
        else:
            synopsis = help_constants.NO_SYNOPSIS

        return ManPage(
            d["source"],
            d["name"],
            synopsis,
            paragraphs,
            [tuple(x) for x in d["aliases"]],
            d["partial_match"],
            d["multi_cmd"],
            d["updated"],
            d.get("nested_cmd"),
        )

    @staticmethod
    def from_store_name_only(name, source):
        return ManPage(source, name, None, [], [], None, None, None)

    def __repr__(self):
        return f"<manpage {self.name}({self.section}), {len(self.options)} options>"


class Store:
    """read/write processed man pages from mongodb

    we use three collections:
    1) classifier - contains manually tagged paragraphs from man pages
    2) manpage - contains a processed man page
    3) mapping - contains (name, manpageid, score) tuples
    """

    def __init__(self, db="explainshell", host=config.MONGO_URI):
        logger.info("creating store, db = %r, host = %r", db, host)
        self.connection = pymongo.MongoClient(host)
        self.db = self.connection[db]
        self.classifier = self.db["classifier"]
        self.manpage = self.db["manpage"]
        self.mapping = self.db["mapping"]

    def close(self):
        self.connection.disconnect()
        self.classifier = self.manpage = self.mapping = self.db = None

    def drop(self, confirm=False):
        if not confirm:
            return

        logger.info("dropping mapping, manpage, collections")
        self.mapping.drop()
        self.manpage.drop()

    def trainingset(self):
        for d in self.classifier.find():
            yield ClassifierManpage.from_store(d)

    def __contains__(self, name):
        c = self.mapping.find({"src": name}).count()
        return c > 0

    def __iter__(self):
        for d in self.manpage.find():
            yield ManPage.from_store(d)

    def find_man_page(self, name):
        """find a man page by its name, everything following the last dot (.) in name,
        is taken as the section of the man page

        we return the man page found with the highest score, and a list of
        suggestions that also matched the given name (only the first item
        is prepopulated with the option data)"""
        if name.endswith(".gz"):
            logger.info("name ends with .gz, looking up an exact match by source")
            d = self.manpage.find_one({"source": name})
            if not d:
                raise errors.ProgramDoesNotExist(name)
            m = ManPage.from_store(d)
            logger.info("returning %s", m)
            return [m]

        section = None
        orig_name = name

        # don't try to look for a section if it's . (source)
        if name != ".":
            splitted = name.rsplit(".", 1)
            name = splitted[0]
            if len(splitted) > 1:
                section = splitted[1]

        logger.info("looking up manpage in mapping with src %r", name)
        cursor = self.mapping.find({"src": name})

        logger.info(list(cursor))
        count = len(list(cursor))
        if count == 0:
            raise errors.ProgramDoesNotExist(name)

        dsts = {d["dst"]: d["score"] for d in cursor}
        cursor = self.manpage.find(
            {"_id": {"$in": list(dsts.keys())}}, {"name": 1, "source": 1}
        )
        if len(list(cursor)) != len(dsts):
            logger.error(
                "one of %r mappings is missing in manpage collection "
                "(%d mappings, %d found)",
                dsts,
                len(dsts),
                cursor.count(),
            )
        results = [(d.pop("_id"), ManPage.from_store_name_only(**d)) for d in cursor]
        results.sort(key=lambda x: dsts.get(x[0], 0), reverse=True)
        logger.info("got %s", results)
        if section is not None:
            if len(results) > 1:
                results.sort(
                    key=lambda oid_m: oid_m[1].section == section, reverse=True
                )
                logger.info(r"sorting %r so %s is first", results, section)
            if results[0][1].section != section:
                raise errors.ProgramDoesNotExist(orig_name)
            results.extend(self._discover_manpage_suggestions(results[0][0], results))

        oid = results[0][0]
        results = [x[1] for x in results]
        results[0] = ManPage.from_store(self.manpage.find_one({"_id": oid}))
        return results

    def _discover_manpage_suggestions(self, oid, existing):
        """find suggestions for a given man page

        oid is the objectid of the man page in question,
        existing is a list of (oid, man page) of suggestions that were
        already discovered
        """
        skip = {oid for oid, m in existing}
        cursor = self.mapping.find({"dst": oid})
        # find all srcs that point to oid
        srcs = [d["src"] for d in cursor]
        # find all dsts of srcs
        suggestionoids = self.mapping.find({"src": {"$in": srcs}}, {"dst": 1})
        # remove already discovered
        suggestionoids = [d["dst"] for d in suggestionoids if d["dst"] not in skip]
        if not suggestionoids:
            return []

        # get just the name and source of found suggestions
        suggestionoids = self.manpage.find(
            {"_id": {"$in": suggestionoids}}, {"name": 1, "source": 1}
        )
        return [
            (d.pop("_id"), ManPage.from_store_name_only(**d)) for d in suggestionoids
        ]

    def add_mapping(self, src, dst, score):
        self.mapping.insert({"src": src, "dst": dst, "score": score})

    def addmanpage(self, m):
        """add m into the store, if it exists first remove it and its mappings

        each man page may have aliases besides the name determined by its
        basename"""
        d = self.manpage.find_one({"source": m.source})
        if d:
            logger.info("removing old manpage %s (%s)", m.source, d["_id"])
            self.manpage.remove(d["_id"])

            # remove old mappings if there are any
            c = self.mapping.count()
            self.mapping.remove({"dst": d["_id"]})
            c -= self.mapping.count()
            logger.info("removed %d mappings for manpage %s", c, m.source)

        o = self.manpage.insert(m.to_store())

        for alias, score in m.aliases:
            self.add_mapping(alias, o, score)
            logger.info(
                "inserting mapping (alias) %s -> %s (%s) with score %d",
                alias,
                m.name,
                o,
                score,
            )
        return m

    def update_man_page(self, m):
        """update m and add new aliases if necessary

        change updated attribute so we don't overwrite this in the future"""
        logger.info("updating manpage %s", m.source)
        m.updated = True
        self.manpage.update({"source": m.source}, m.to_store())
        _id = self.manpage.find_one({"source": m.source}, fields={"_id": 1})["_id"]
        for alias, score in m.aliases:
            if alias not in self:
                self.add_mapping(alias, _id, score)
                logger.info(
                    "inserting mapping (alias) %s -> %s (%s) with score %d",
                    alias,
                    m.name,
                    _id,
                    score,
                )
            else:
                logger.debug(
                    "mapping (alias) %s -> %s (%s) already exists", alias, m.name, _id
                )
        return m

    def verify(self):
        # check that everything in manpage is reachable
        mappings = list(self.mapping.find())
        reachable = {m["dst"] for m in mappings}
        man_pages = {m["_id"] for m in self.manpage.find(fields={"_id": 1})}

        ok = True
        unreachable = man_pages - reachable
        if unreachable:
            logger.error(
                "manpages %r are unreachable (nothing maps to them)", unreachable
            )
            unreachable = [
                self.manpage.find_one({"_id": u})["name"] for u in unreachable
            ]
            ok = False

        notfound = reachable - man_pages
        if notfound:
            logger.error("mappings to inexisting manpages: %r", notfound)
            ok = False

        return ok, unreachable, notfound

    def names(self):
        cursor = self.manpage.find(fields={"name": 1})
        for d in cursor:
            yield d["_id"], d["name"]

    def mappings(self):
        cursor = self.mapping.find(fields={"src": 1})
        for d in cursor:
            yield d["src"], d["_id"]

    def setmulti_cmd(self, manpage_id):
        self.manpage.update({"_id": manpage_id}, {"$set": {"multi_cmd": True}})
