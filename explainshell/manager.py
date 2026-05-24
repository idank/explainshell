import sys, os, argparse, logging, glob

from explainshell import options, store, fixer, manpage, errors, util, config
from explainshell.algo import classifier

logger = logging.getLogger('explainshell.manager')

class managerctx(object):
    def __init__(self, classifier, store, manpage):
        self.classifier = classifier
        self.store = store
        self.manpage = manpage
        self.name = manpage.name

        self.classifiermanpage = None
        self.optionsraw = None
        self.optionsextracted = None
        self.aliases = None

class manager(object):
    '''the manager uses all parts of the system to read, classify, parse, extract
    and write a man page to the database'''
    def __init__(self, dbhost, dbname, paths, overwrite=False, drop=False):
        self.paths = paths
        self.overwrite = overwrite

        self.store = store.store(dbname, dbhost)

        self.classifier = classifier.classifier(self.store, 'bayes')
        self.classifier.train()

        if drop:
            self.store.drop(True)

    def ctx(self, m):
        return managerctx(self.classifier, self.store, m)

    def _read(self, ctx, frunner):
        frunner.pre_get_raw_manpage()
        ctx.manpage.read()
        ctx.manpage.parse()
        assert len(ctx.manpage.paragraphs) > 1

        ctx.manpage = store.manpage(ctx.manpage.shortpath, ctx.manpage.name,
                ctx.manpage.synopsis, ctx.manpage.paragraphs, list(ctx.manpage.aliases))
        frunner.post_parse_manpage()

    def _classify(self, ctx, frunner):
        ctx.classifiermanpage = store.classifiermanpage(ctx.name, ctx.manpage.paragraphs)
        frunner.pre_classify()
        _ = list(ctx.classifier.classify(ctx.classifiermanpage))
        frunner.post_classify()

    def _extract(self, ctx, frunner):
        options.extract(ctx.manpage)
        frunner.post_option_extraction()
        if not ctx.manpage.options:
            logger.warn("couldn't find any options for manpage %s", ctx.manpage.name)

    def _write(self, ctx, frunner):
        frunner.pre_add_manpage()
        return ctx.store.addmanpage(ctx.manpage)

    def _update(self, ctx, frunner):
        frunner.pre_add_manpage()
        return ctx.store.updatemanpage(ctx.manpage)

    def process(self, ctx):
        frunner = fixer.runner(ctx)

        self._read(ctx, frunner)
        self._classify(ctx, frunner)
        self._extract(ctx, frunner)

        m = self._write(ctx, frunner)
        return m

    def edit(self, m, paragraphs=None):
        ctx = self.ctx(m)
        frunner = fixer.runner(ctx)

        if paragraphs:
            m.paragraphs = paragraphs
            frunner.disable('paragraphjoiner')
            frunner.post_option_extraction()
        else:
            self._extract(ctx, frunner)
        m = self._update(ctx, frunner)
        return m

    def run(self):
        added = []
        exists = []
        for path in self.paths:
            try:
                m = manpage.manpage(path)
                logger.info('handling manpage %s (from %s)', m.name, path)
                try:
                    mps = self.store.findmanpage(m.shortpath[:-3])
                    mps = [mp for mp in mps if m.shortpath == mp.source]
                    if mps:
                        assert len(mps) == 1
                        mp = mps[0]
                        if not self.overwrite or mp.updated:
                            logger.info('manpage %r already in the data store, not overwriting it', m.name)
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
                logger.error('manpage %r is empty!', e.args[0])
            except ValueError:
                logger.fatal('uncaught exception when handling manpage %s', path)
            except KeyboardInterrupt:
                raise
            except:
                logger.fatal('uncaught exception when handling manpage %s', path)
                raise
        if not added:
            logger.warn('no manpages added')
        else:
            self.findmulticommands()

        return added, exists

    def findmulticommands(self):
        manpages = {}
        potential = []
        for _id, m in self.store.names():
            if '-' in m:
                potential.append((m.split('-'), _id))
            else:
                manpages[m] = _id

        mappings = set([x[0] for x in self.store.mappings()])
        mappingstoadd = []
        multicommands = {}

        for p, _id in potential:
            if ' '.join(p) in mappings:
                continue
            if p[0] in manpages:
                mappingstoadd.append((' '.join(p), _id))
                multicommands[p[0]] = manpages[p[0]]

        for src, dst in mappingstoadd:
            self.store.addmapping(src, dst, 1)
            logger.info('inserting mapping (multicommand) %s -> %s', src, dst)

        for multicommand, _id in multicommands.items():
            self.store.setmulticommand(_id)
            logger.info('making %r a multicommand', multicommand)

        return mappingstoadd, multicommands

def main(files, dbname, dbhost, overwrite, drop, verify):
    if verify:
        s = store.store(dbname, dbhost)
        ok = s.verify()
        return 0 if ok else 1

    if drop:
        if input('really drop db (y/n)? ').strip().lower() != 'y':
            drop = False
        else:
            overwrite = True # if we drop, no need to take overwrite into account

    gzs = set()

    for path in files:
        if os.path.isdir(path):
            gzs.update([os.path.abspath(f) for f in glob.glob(os.path.join(path, '*.gz'))])
        else:
            gzs.add(os.path.abspath(path))

    m = manager(dbhost, dbname, gzs, overwrite, drop)
    added, exists = m.run()
    for mp in added:
        print('successfully added %s' % mp.source)
    if exists:
        print('these manpages already existed and werent overwritten: \n\n%s' % '\n'.join([m.path for m in exists]))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='process man pages and save them in the store')
    parser.add_argument('--log', type=str, default='ERROR', help='use log as the logger log level')
    parser.add_argument('--overwrite', action='store_true', default=False, help='overwrite man pages that already exist in the store')
    parser.add_argument('--drop', action='store_true', default=False, help='delete all existing man pages')
    parser.add_argument('--db', default='explainshell', help='mongo db name')
    parser.add_argument('--host', default=config.MONGO_URI, help='mongo host')
    parser.add_argument('--verify', action='store_true', default=False, help='verify db integrity')
    parser.add_argument('files', nargs='*')

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log.upper()))
    sys.exit(main(args.files, args.db, args.host, args.overwrite, args.drop, args.verify))
