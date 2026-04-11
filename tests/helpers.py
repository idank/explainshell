import datetime
import os

from explainshell.models import Option, ParsedManpage, RawManpage
from explainshell.store import Store

TESTS_DIR = os.path.dirname(__file__)

_NOW = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
_ROFF_RAW = RawManpage(
    source_text=".TH roff content", generated_at=_NOW, generator="roff"
)
_MARKDOWN_RAW = RawManpage(
    source_text="# markdown content", generated_at=_NOW, generator="mandoc -T markdown"
)


def create_test_store() -> Store:
    """Build a real Store backed by in-memory SQLite with standard test fixtures."""
    store = Store.create(":memory:")

    opts = [
        Option(text="-a desc", short=["-a"], long=["--a"], has_argument=False),
        Option(text="-b <arg> desc", short=["-b"], long=["--b"], has_argument=True),
        Option(text="-? help text", short=["-?"], long=[], has_argument=False),
        Option(
            text="-c=one,two\ndesc",
            short=["-c"],
            long=[],
            has_argument=["one", "two"],
        ),
    ]

    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/bar.1.gz",
            name="bar",
            synopsis="bar synopsis",
            options=opts,
            aliases=[("bar", 10)],
            subcommands=["foo"],
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/baz.1.gz",
            name="baz",
            synopsis="baz synopsis",
            options=opts,
            aliases=[("baz", 10)],
            dashless_opts=True,
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/bar-foo.1.gz",
            name="bar-foo",
            synopsis="bar foo synopsis",
            options=opts,
            aliases=[("bar foo", 10)],
            dashless_opts=True,
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/nosynopsis.1.gz",
            name="nosynopsis",
            synopsis=None,
            options=opts,
            aliases=[("nosynopsis", 10)],
        ),
        _ROFF_RAW,
    )

    # Two manpages with the same name but different sections (suggestions).
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/dup.1.gz",
            name="dup",
            synopsis="dup1 synopsis",
            options=opts,
            aliases=[("dup", 10)],
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/2/dup.2.gz",
            name="dup",
            synopsis="dup2 synopsis",
            options=opts,
            aliases=[("dup", 5)],
        ),
        _ROFF_RAW,
    )

    extended_opts = list(opts)
    extended_opts.append(
        Option(
            text="FILE argument",
            short=[],
            long=[],
            has_argument=False,
            positional="FILE",
        )
    )
    extended_opts.append(
        Option(
            text="-exec nest",
            short=["-exec"],
            long=[],
            has_argument=True,
            nested_cmd=["EOF", ";"],
        )
    )

    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/markdown-page.1.gz",
            name="markdown-page",
            synopsis="page with markdown source",
            options=extended_opts,
            aliases=[("markdown-page", 10)],
        ),
        _MARKDOWN_RAW,
    )

    # Edge-case filenames.
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/cd.1posix.gz",
            name="cd",
            synopsis="change directory",
            options=[],
            aliases=[("cd", 10)],
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/pg_autoctl create worker.1.gz",
            name="pg_autoctl create worker",
            synopsis="create a worker",
            options=[],
            aliases=[("pg_autoctl create worker", 10)],
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/c++filt.1.gz",
            name="c++filt",
            synopsis="demangle symbols",
            options=[],
            aliases=[("c++filt", 10)],
        ),
        _ROFF_RAW,
    )
    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/withargs.1.gz",
            name="withargs",
            synopsis="withargs synopsis",
            options=extended_opts,
            aliases=[("withargs", 10)],
            dashless_opts=True,
            nested_cmd=True,
        ),
        _ROFF_RAW,
    )

    multipos_opts = list(opts)
    multipos_opts.append(
        Option(
            text="source file(s) to copy",
            short=[],
            long=[],
            has_argument=False,
            positional="SOURCE",
        )
    )
    multipos_opts.append(
        Option(
            text="destination path",
            short=[],
            long=[],
            has_argument=False,
            positional="DEST",
        )
    )

    store.add_manpage(
        ParsedManpage(
            source="ubuntu/25.10/1/withmultipos.1.gz",
            name="withmultipos",
            synopsis="withmultipos synopsis",
            options=multipos_opts,
            aliases=[("withmultipos", 10)],
        ),
        _ROFF_RAW,
    )

    return store


s = create_test_store()
