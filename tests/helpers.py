from explainshell import store, errors


class MockStore:
    def __init__(self):
        so = store.Option
        sm = store.ParsedManpage

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

    def find_man_page(self, x, section=None, distro=None, release=None):
        try:
            if x == "dup":
                return self.dup
            return [self.manpages[x]]
        except KeyError:
            raise errors.ProgramDoesNotExist(x)


s = MockStore()
