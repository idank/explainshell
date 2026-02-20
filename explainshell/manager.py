import argparse
import os
import sys
import logging
import glob

from explainshell import options, store, fixer, manpage, errors, config
from explainshell.algo import classifier

logger = logging.getLogger("explainshell.manager")


class ManagerCtx:
    def __init__(self, classifier, store, manpage):
        self.classifier = classifier
        self.store = store
        self.manpage = manpage
        self.name = manpage.name

        self.classifier_man_page = None
        self.options_raw = None
        self.options_extracted = None
        self.aliases = None


class Manager:
    """the manager uses all parts of the system to read, classify, parse, extract
    and write a man page to the database"""

    def __init__(self, db_host, dbname, paths, overwrite=False, drop=False):
        self.paths = paths
        self.overwrite = overwrite

        self.store = store.Store(dbname, db_host)

        self.classifier = classifier.Classifier(self.store, "bayes")
        self.classifier.train()

        if drop:
            self.store.drop(True)

    def ctx(self, m):
        return ManagerCtx(self.classifier, self.store, m)

    def _read(self, ctx, f_runner):
        f_runner.pre_get_raw_manpage()
        ctx.manpage.read()
        ctx.manpage.parse()
        assert len(ctx.manpage.paragraphs) > 1

        ctx.manpage = store.ManPage(
            ctx.manpage.short_path,
            ctx.manpage.name,
            ctx.manpage.synopsis,
            ctx.manpage.paragraphs,
            list(ctx.manpage.aliases),
        )
        f_runner.post_parse_manpage()

    def _classify(self, ctx, fr_runner):
        ctx.classifiermanpage = store.ClassifierManpage(
            ctx.name, ctx.manpage.paragraphs
        )
        fr_runner.pre_classify()
        _ = list(ctx.classifier.classify(ctx.classifiermanpage))
        fr_runner.post_classify()

    def _extract(self, ctx, f_runner):
        options.extract(ctx.manpage)
        f_runner.post_option_extraction()
        if not ctx.manpage.options:
            logger.warning("couldn't find any options for manpage %s", ctx.manpage.name)

    def _write(self, ctx, f_runner):
        f_runner.pre_add_manpage()
        return ctx.store.add_manpage(ctx.manpage)

    def _update(self, ctx, f_runner):
        f_runner.pre_add_manpage()
        return ctx.store.updatemanpage(ctx.manpage)

    def process(self, ctx):
        f_runner = fixer.Runner(ctx)

        self._read(ctx, f_runner)
        self._classify(ctx, f_runner)
        self._extract(ctx, f_runner)

        m = self._write(ctx, f_runner)
        return m

    def edit(self, m, paragraphs=None):
        ctx = self.ctx(m)
        f_runner = fixer.Runner(ctx)

        if paragraphs:
            m.paragraphs = paragraphs
            f_runner.disable("paragraphjoiner")
            f_runner.post_option_extraction()
        else:
            self._extract(ctx, f_runner)
        m = self._update(ctx, f_runner)
        return m

    def run(self):
        added = []
        exists = []
        for path in self.paths:
            try:
                m = manpage.ManPage(path)
                logger.info("handling manpage %s (from %s)", m.name, path)
                try:
                    mps = self.store.find_man_page(m.short_path[:-3])
                    mps = [mp for mp in mps if m.short_path == mp.source]
                    if mps:
                        assert len(mps) == 1
                        mp = mps[0]
                        if not self.overwrite or mp.updated:
                            logger.info(
                                "manpage %r already in the data store, not overwriting it",
                                m.name,
                            )
                            exists.append(m)
                            continue
                except errors.ProgramDoesNotExist:
                    pass

                # the manpage is not in the data store; process and add it
                ctx = self.ctx(m)
                m = self.process(ctx)
                if m:
                    added.append(m)
            except errors.EmptyManpage as e:
                logger.error("manpage %r is empty!", e.args[0])
            except ValueError:
                logger.fatal("uncaught exception when handling manpage %s", path)
            except KeyboardInterrupt:
                raise
            except Exception as error_msg:
                logger.fatal(f"uncaught exception when handling manpage '{path}' -> error: {error_msg}")
                raise
        if not added:
            logger.warning("no manpages added")
        else:
            self.findmulti_cmds()

        return added, exists

    def findmulti_cmds(self):
        manpages = {}
        potential = []
        for _id, m in self.store.names():
            if "-" in m:
                potential.append((m.split("-"), _id))
            else:
                manpages[m] = _id

        mappings = {x[0] for x in self.store.mappings()}
        mappings_to_a = []
        multi_cmds = {}

        for p, _id in potential:
            if " ".join(p) in mappings:
                continue
            if p[0] in manpages:
                mappings_to_a.append((" ".join(p), _id))
                multi_cmds[p[0]] = manpages[p[0]]

        for src, dst in mappings_to_a:
            self.store.add_mapping(src, dst, 1)
            logger.info("inserting mapping (multi_cmd) %s -> %s", src, dst)

        for multi_cmd, _id in multi_cmds.items():
            self.store.set_multi_cmd(_id)
            logger.info("making %r a multi_cmd", multi_cmd)

        return mappings_to_a, multi_cmds


def main(files, dbname, db_host, overwrite, drop, verify):
    if verify:
        s = store.Store(dbname, db_host)
        ok = s.verify()
        return 0 if ok else 1

    if drop:
        if input("really drop db (y/n)? ").strip().lower() != "y":
            drop = False
        else:
            overwrite = True  # if we drop, no need to take overwrite into account

    gzs = set()

    for path in files:
        if os.path.isdir(path):
            gzs.update(
                [os.path.abspath(f) for f in glob.glob(os.path.join(path, "*.gz"))]
            )
        else:
            gzs.add(os.path.abspath(path))

    m = Manager(db_host, dbname, gzs, overwrite, drop)
    added, exists = m.run()
    for mp in added:
        print(f"successfully added '{mp.source}'")
    if exists:
        print(
            "these manpages already existed and were not overwritten: \n\n%s"
            % "\n".join([m.path for m in exists])
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="process man pages and save them in the store"
    )
    parser.add_argument(
        "--log", type=str, default="ERROR", help="use log as the logger log level"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="overwrite man pages that already exist in the store",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        default=False,
        help="delete all existing man pages",
    )
    parser.add_argument("--db", default="explainshell", help="mongo db name")
    parser.add_argument("--host", default=config.MONGO_URI, help="mongo host")
    parser.add_argument(
        "--verify", action="store_true", default=False, help="verify db integrity"
    )
    parser.add_argument("files", nargs="*")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log.upper()))
    sys.exit(
        main(args.files, args.db, args.host, args.overwrite, args.drop, args.verify)
    )
