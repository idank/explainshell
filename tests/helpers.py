from explainshell import store, errors


class MockStore:
    def __init__(self):
        so = store.Option
        sm = store.ParsedManpage

        opts = [
            so(text="-a desc", short=["-a"], long=["--a"], expects_arg=False),
            so(text="-b <arg> desc", short=["-b"], long=["--b"], expects_arg="<arg>"),
            so(text="-? help text", short=["-?"], long=[], expects_arg=False),
            so(
                text="-c=one,two\ndesc",
                short=["-c"],
                long=[],
                expects_arg=["one", "two"],
            ),
        ]
        self.manpages = {
            "bar": sm(
                source="ubuntu/25.10/1/bar.1.gz",
                name="bar",
                synopsis="bar synopsis",
                options=opts,
                multi_cmd=True,
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
                expects_arg=False,
                argument="FILE",
            )
        )
        opts.append(
            so(
                text="-exec nest",
                short=["-exec"],
                long=[],
                expects_arg=True,
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
