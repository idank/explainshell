import unittest, os

from explainshell import manager, config, store, errors

class test_manager(unittest.TestCase):
    def setUp(self):
        store.store('explainshell_tests').drop(True)

    def _getmanager(self, names, **kwargs):
        l = []
        for n in names:
            l.append(os.path.join(config.MANPAGEDIR, '1', n))

        m = manager.manager(config.MONGO_URI, 'explainshell_tests', l, **kwargs)
        return m

    def test(self):
        m = self._getmanager(['tar.1.gz'])
        m.run()

        self.assertRaises(errors.ProgramDoesNotExist, m.store.findmanpage, 'tar.2')
        mp = m.store.findmanpage('tar')[0]
        self.assertEquals(mp.source, 'tar.1.gz')
        self.assertEquals(mp.name, 'tar')
        self.assertEquals(mp.aliases, [('tar', 10)])
        self.assertEquals(len(mp.paragraphs), 154)
        self.assertEquals(len(mp.options), 134)
        self.assertTrue(mp.find_option('-v'))
        self.assertEquals(mp.synopsis, 'The GNU version of the tar archiving utility')

        self.assertTrue(mp.partialmatch) # fixer is working

        self.assertEquals(m.run()[0], [])

    def test_verify(self):
        m = self._getmanager(['tar.1.gz'])
        s = m.store

        # invalid mapping
        s.addmapping('foo', 'bar', 1)
        ok, unreachable, notfound = s.verify()
        self.assertFalse(ok)
        self.assertEquals(list(notfound), ['bar'])

        s.mapping.drop()
        m.run()
        ok, unreachable, notfound = s.verify()
        self.assertTrue(ok)

        s.mapping.drop()
        ok, unreachable, notfound = s.verify()
        self.assertEquals(list(unreachable), ['tar'])

        s.addmapping('foo', 'bar', 1)
        ok, unreachable, notfound = s.verify()
        self.assertEquals(list(notfound), ['bar'])
        self.assertEquals(list(unreachable), ['tar'])

    @unittest.skip("https://github.com/idank/explainshell/pull/303#issuecomment-1272387073")
    def test_aliases(self):
        m = self._getmanager(['lsbcpp.1.gz', 'tar.1.gz', 'bsdtar.1.gz', 'basket.1.gz'])
        m.run()

        mp = m.store.findmanpage('lsbcpp')
        self.assertTrue('lsbcc' in m.store)
        self.assertTrue('lsbc++' in m.store)
        self.assertTrue('lsbcpp' in m.store)
        self.assertEquals(len(mp), 1)

        mp = m.store.findmanpage('tar')
        self.assertEquals(len(mp), 2)
        self.assertEquals(mp[0].source, 'tar.1.gz')
        self.assertEquals(mp[1].source, 'bsdtar.1.gz')

    def test_overwrite(self):
        m = self._getmanager(['tar.1.gz'], overwrite=False)
        self.assertEquals(len(list(m.store)), 0)

        a, e = m.run()
        self.assertTrue(a)
        self.assertFalse(e)
        self.assertEquals(m.store.mapping.count(), 1)
        self.assertEquals(len(list(m.store)), 1)

        a, e = m.run()
        self.assertFalse(a)
        self.assertTrue(e)
        self.assertEquals(m.store.mapping.count(), 1)
        self.assertEquals(len(list(m.store)), 1)

        m = manager.manager(config.MONGO_URI, 'explainshell_tests', [os.path.join(config.MANPAGEDIR, '1', 'tar.1.gz')], overwrite=True)
        a, e = m.run()
        self.assertTrue(a)
        self.assertFalse(e)
        self.assertEquals(m.store.mapping.count(), 1)
        self.assertEquals(len(list(m.store)), 1)

        m.store.verify()

    def test_multicommand(self):
        m = self._getmanager(['git.1.gz', 'git-rebase.1.gz'])
        m.run()

        self.assertTrue(m.store.findmanpage('git')[0].multicommand)
        self.assertTrue('git rebase' in m.store)

    def test_edit(self):
        m = self._getmanager(['tar.1.gz'], overwrite=False)
        self.assertEquals(len(list(m.store)), 0)

        a, e = m.run()
        mp = a[0]
        mp.synopsis = 'foo'
        m.edit(mp)

        mp = m.store.findmanpage('tar')[0]
        self.assertEquals(mp.synopsis, 'foo')
        self.assertTrue(m.store.verify())

        mp.aliases.append(('foo', 1))
        m.edit(mp)
        self.assertTrue('foo' in m.store)
        self.assertEquals(m.store.findmanpage('tar')[0].paragraphs,
                          m.store.findmanpage('foo')[0].paragraphs)
        self.assertTrue(m.store.verify()[0])

    def test_samename(self):
        pages = [os.path.join(config.MANPAGEDIR, '1', 'node.1.gz'), os.path.join(config.MANPAGEDIR, '8', 'node.8.gz')]
        m = manager.manager(config.MONGO_URI, 'explainshell_tests', pages)
        a, e = m.run()
        self.assertEquals(len(a), 2)
        self.assertEquals(len(m.store.findmanpage('node')), 2)
        mps = m.store.findmanpage('node.8')
        self.assertEquals(len(mps), 2)
        self.assertEquals(mps[0].section, '8')

    def test_samename_samesection(self):
        m = self._getmanager(['xargs.1.gz', 'xargs.1posix.gz'])
        a, e = m.run()
        self.assertEquals(len(a), 2)
        self.assertEquals(len(m.store.findmanpage('xargs')), 2)
        mps = m.store.findmanpage('xargs.1posix')
        self.assertEquals(len(mps), 2)
        self.assertEquals(mps[0].section, '1posix')
