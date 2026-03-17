import os

from explainshell import models, errors

TESTS_DIR = os.path.dirname(__file__)


class MockStore:
    def __init__(self):
        so = models.Option
        sm = models.ParsedManpage

        opts = [
            so(text="-a desc", short=["-a"], long=["--a"], has_argument=False),
            so(text="-b <arg> desc", short=["-b"], long=["--b"], has_argument=True),
            so(text="-? help text", short=["-?"], long=[], has_argument=False),
            so(
                text="-c=one,two\ndesc",
                short=["-c"],
                long=[],
                has_argument=["one", "two"],
            ),
        ]
        self.manpages = {
            "bar": sm(
                source="ubuntu/25.10/1/bar.1.gz",
                name="bar",
                synopsis="bar synopsis",
                options=opts,
                has_subcommands=True,
            ),
            "baz": sm(
                source="ubuntu/25.10/1/baz.1.gz",
                name="baz",
                synopsis="baz synopsis",
                options=opts,
                dashless_opts=True,
            ),
            "bar foo": sm(
                source="ubuntu/25.10/1/bar-foo.1.gz",
                name="bar-foo",
                synopsis="bar foo synopsis",
                options=opts,
                dashless_opts=True,
            ),
            "nosynopsis": sm(
                source="ubuntu/25.10/1/bar.1.gz",
                name="bar",
                synopsis=None,
                options=opts,
            ),
        }

        self.dup = [
            sm(
                source="ubuntu/25.10/1/dup.1.gz",
                name="dup",
                synopsis="dup1 synopsis",
                options=opts,
            ),
            sm(
                source="ubuntu/25.10/2/dup.2.gz",
                name="dup",
                synopsis="dup2 synopsis",
                options=opts,
            ),
        ]

        opts = list(opts)
        opts.append(
            so(
                text="FILE argument",
                short=[],
                long=[],
                has_argument=False,
                positional="FILE",
            )
        )
        opts.append(
            so(
                text="-exec nest",
                short=["-exec"],
                long=[],
                has_argument=True,
                nested_cmd=["EOF", ";"],
            )
        )
        self.manpages["markdown-page"] = sm(
            source="ubuntu/25.10/1/markdown-page.1.gz",
            name="markdown-page",
            synopsis="page with markdown source",
            options=opts,
        )
        # Edge-case filenames: mismatched dir/file section, spaces, and +
        self.manpages["cd"] = sm(
            source="ubuntu/25.10/1/cd.1posix.gz",
            name="cd",
            synopsis="change directory",
            options=[],
        )
        self.manpages["pg_autoctl create worker"] = sm(
            source="ubuntu/25.10/1/pg_autoctl create worker.1.gz",
            name="pg_autoctl create worker",
            synopsis="create a worker",
            options=[],
        )
        self.manpages["c++filt"] = sm(
            source="ubuntu/25.10/1/c++filt.1.gz",
            name="c++filt",
            synopsis="demangle symbols",
            options=[],
        )
        self.manpages["withargs"] = sm(
            source="ubuntu/25.10/1/withargs.1.gz",
            name="withargs",
            synopsis="withargs synopsis",
            options=opts,
            dashless_opts=True,
            nested_cmd=True,
        )

    def distros(self):
        return [("ubuntu", "25.10")]

    def get_manpage_source(self, source: str) -> tuple[str, str] | None:
        for mp in self.manpages.values():
            if mp.source == source:
                if "markdown" in mp.name:
                    return "# markdown content", "mandoc -T markdown"
                return ".TH roff content", "roff"
        return None

    def list_sections(self, distro: str, release: str) -> list[str]:
        prefix = f"{distro}/{release}/"
        sections = set()
        for mp in self.manpages.values():
            if mp.source.startswith(prefix):
                rest = mp.source[len(prefix) :]
                sections.add(rest.split("/")[0])
        return sorted(sections)

    def list_manpages(self, prefix: str) -> list[str]:
        return [
            mp.source for mp in self.manpages.values() if mp.source.startswith(prefix)
        ]

    def find_man_page(self, x, section=None, distro=None, release=None):
        try:
            if x == "dup":
                return self.dup
            return [self.manpages[x]]
        except KeyError:
            raise errors.ProgramDoesNotExist(x)


s = MockStore()
