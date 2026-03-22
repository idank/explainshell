"""Core domain types for man pages and options.

These are pure data classes with no database dependencies, used across
the extraction pipeline, web layer, store, and tests.
"""

import collections
import dataclasses
import datetime
import json
import os

from pydantic import BaseModel

from explainshell import help_constants, util


@dataclasses.dataclass
class RawManpage:
    source_text: str
    generated_at: datetime.datetime
    generator: str
    generator_version: str | None = None
    source_gz_sha256: str | None = None


class Option(BaseModel):
    """An extracted command-line option from a man page.

    short - a list of short options (-a, -b, ..)
    long - a list of long options (--a, --b)
    has_argument - specifies if one of the short/long options expects an additional argument
    positional - specifies if to consider this as positional arguments
    nested_cmd - specifies if the arguments to this option can start a nested command
    """

    text: str
    short: list[str] = []
    long: list[str] = []
    has_argument: bool | list[str] = False
    positional: str | bool | None = None
    nested_cmd: bool | list[str] = False
    meta: dict | None = None

    @property
    def opts(self) -> list[str]:
        return self.short + self.long

    def __str__(self):
        return "(" + ", ".join([str(x) for x in self.opts]) + ")"

    def __repr__(self):
        return f"<option {self}>"


class ParsedManpage(BaseModel):
    """processed man page

    source - the path to the original source man page
    name - the name of this man page as extracted by manpage.manpage
    synopsis - the synopsis of this man page as extracted by manpage.manpage
    options - a list of options extracted from this man page
    aliases - a list of aliases found for this man page
    dashless_opts - allow interpreting options without a leading '-'
    subcommands - list of subcommand names extracted from the manpage; when non-empty,
        the matcher looks ahead for e.g. "git commit" and resolves it to the git-commit manpage
    updated - whether this man page was manually updated
    nested_cmd - specifies if positional arguments to this program can start a nested command,
        e.g. sudo, xargs
    """

    source: str
    name: str
    synopsis: str | None = None
    options: list[Option] = []
    aliases: list[tuple[str, int]] = []
    dashless_opts: bool = False
    subcommands: list[str] = []
    updated: bool = False
    nested_cmd: bool | str = False
    extractor: str | None = None
    extraction_meta: dict | None = None

    @property
    def name_section(self):
        name, section = util.name_section(os.path.basename(self.source)[:-3])
        return f"{name}({section})"

    @property
    def section(self):
        name, section = util.name_section(os.path.basename(self.source)[:-3])
        return section

    @property
    def positionals(self):
        # go over all options and look for those with the same 'positional' field
        groups = collections.OrderedDict()
        for opt in self.options:
            if opt.positional:
                groups.setdefault(opt.positional, []).append(opt)

        # merge all the options under the same argument to a single string
        for k, ln in groups.items():
            groups[k] = "\n\n".join([p.text for p in ln])

        return groups

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
            "options": json.dumps([o.model_dump() for o in self.options]),
            "aliases": json.dumps(self.aliases),
            "dashless_opts": int(bool(self.dashless_opts)),
            "subcommands": json.dumps(self.subcommands),
            "updated": int(bool(self.updated)),
            "nested_cmd": json.dumps(self.nested_cmd),
            "extractor": self.extractor,
            "extraction_meta": json.dumps(self.extraction_meta or {}),
        }

    @staticmethod
    def from_store(d):
        options = []
        for od in json.loads(d["options"]):
            options.append(Option.model_validate(od))

        synopsis = d["synopsis"]
        if not synopsis:
            synopsis = help_constants.NO_SYNOPSIS

        dashless_opts = bool(d["dashless_opts"])
        subcommands = json.loads(d["subcommands"])
        nested_cmd = json.loads(d["nested_cmd"])

        extraction_meta_raw = d["extraction_meta"]
        extraction_meta = json.loads(extraction_meta_raw) if extraction_meta_raw else {}

        return ParsedManpage(
            source=d["source"],
            name=d["name"],
            synopsis=synopsis,
            options=options,
            aliases=[tuple(x) for x in json.loads(d["aliases"])],
            dashless_opts=dashless_opts,
            subcommands=subcommands,
            updated=bool(d["updated"]),
            nested_cmd=nested_cmd,
            extractor=d["extractor"],
            extraction_meta=extraction_meta or None,
        )

    def __repr__(self):
        return f"<manpage {self.name}({self.section}), {len(self.options)} options>"
