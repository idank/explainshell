from explainshell import help_constants, matcher, store, errors, options


class MockStore:
    def __init__(self):
        sp = store.Paragraph
        so = store.Option
        sm = store.ManPage

        p0 = sp(0, "-a desc", "", True)
        p1 = sp(1, "-b <arg> desc", "", True)
        p2 = sp(2, "-? help text", "", True)
        p3 = sp(3, "-c=one,two\ndesc", "", True)
        p4 = sp(4, "FILE argument", "", True)
        p5 = sp(5, "-exec nest", "", True)
        opts = [
            so(p0, ["-a"], ["--a"], False),
            so(p1, ["-b"], ["--b"], "<arg>"),
            so(p2, ["-?"], [], False),
            so(p3, ["-c"], [], ["one", "two"]),
        ]
        self.manpages = {
            "bar": sm("bar.1.gz", "bar", "bar synopsis", opts, [], multi_cmd=True),
            "baz": sm("baz.1.gz", "baz", "baz synopsis", opts, [], partial_match=True),
            "bar foo": sm(
                "bar-foo.1.gz",
                "bar-foo",
                "bar foo synopsis",
                opts,
                [],
                partial_match=True,
            ),
            "nosynopsis": sm("bar.1.gz", "bar", None, opts, []),
        }

        self.dup = [
            sm("dup.1.gz", "dup", "dup1 synopsis", opts, []),
            sm("dup.2.gz", "dup", "dup2 synopsis", opts, []),
        ]

        opts = list(opts)
        opts.append(so(p4, [], [], False, "FILE"))
        opts.append(so(p5, ["-exec"], [], True, nested_cmd=["EOF", ";"]))
        self.manpages["withargs"] = sm(
            "withargs.1.gz",
            "withargs",
            "withargs synopsis",
            opts,
            [],
            partial_match=True,
            nested_cmd=True,
        )

    def find_man_page(self, x, section=None):
        try:
            if x == "dup":
                return self.dup
            return [self.manpages[x]]
        except KeyError:
            raise errors.ProgramDoesNotExist(x)


s = MockStore()
