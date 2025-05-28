import unittest, os

from explainshell import manager, config, store, errors


@unittest.skip("nltk usage is broken due to new version")
class test_manager(unittest.TestCase):
    def setUp(self):
        store.Store("explainshell_tests").drop(True)

    def _getmanager(self, names, **kwargs):
        l = []
        for n in names:
            l.append(os.path.join(config.MAN_PAGE_DIR, "1", n))

        m = manager.Manager(config.MONGO_URI, "explainshell_tests", l, **kwargs)
        return m

    def test(self):
        m = self._getmanager(["tar.1.gz"])
        m.run()

        self.assertRaises(errors.ProgramDoesNotExist, m.store.find_man_page, "tar.2")
        mp = m.store.find_man_page("tar")[0]
        self.assertEqual(mp.source, "tar.1.gz")
        self.assertEqual(mp.name, "tar")
        self.assertEqual(mp.aliases, [("tar", 10)])
        self.assertEqual(len(mp.paragraphs), 154)
        self.assertEqual(len(mp.options), 134)
        self.assertTrue(mp.find_option("-v"))
        self.assertEqual(mp.synopsis, "The GNU version of the tar archiving utility")

        self.assertTrue(mp.partial_match)  # fixer is working

        self.assertEqual(m.run()[0], [])

    def test_verify(self):
        m = self._getmanager(["tar.1.gz"])
        s = m.store

        # invalid mapping
        s.add_mapping("foo", "bar", 1)
        ok, unreachable, notfound = s.verify()
        self.assertFalse(ok)
        self.assertEqual(list(notfound), ["bar"])

        s.mapping.drop()
        m.run()
        ok, unreachable, notfound = s.verify()
        self.assertTrue(ok)

        s.mapping.drop()
        ok, unreachable, notfound = s.verify()
        self.assertEqual(list(unreachable), ["tar"])

        s.add_mapping("foo", "bar", 1)
        ok, unreachable, notfound = s.verify()
        self.assertEqual(list(notfound), ["bar"])
        self.assertEqual(list(unreachable), ["tar"])

    @unittest.skip(
        "https://github.com/idank/explainshell/pull/303#issuecomment-1272387073"
    )
    def test_aliases(self):
        m = self._getmanager(["lsbcpp.1.gz", "tar.1.gz", "bsdtar.1.gz", "basket.1.gz"])
        m.run()

        mp = m.store.find_man_page("lsbcpp")
        self.assertTrue("lsbcc" in m.store)
        self.assertTrue("lsbc++" in m.store)
        self.assertTrue("lsbcpp" in m.store)
        self.assertEqual(len(mp), 1)

        mp = m.store.find_man_page("tar")
        self.assertEqual(len(mp), 2)
        self.assertEqual(mp[0].source, "tar.1.gz")
        self.assertEqual(mp[1].source, "bsdtar.1.gz")

    def test_overwrite(self):
        m = self._getmanager(["tar.1.gz"], overwrite=False)
        self.assertEqual(len(list(m.store)), 0)

        a, e = m.run()
        self.assertTrue(a)
        self.assertFalse(e)
        self.assertEqual(m.store.mapping.count(), 1)
        self.assertEqual(len(list(m.store)), 1)

        a, e = m.run()
        self.assertFalse(a)
        self.assertTrue(e)
        self.assertEqual(m.store.mapping.count(), 1)
        self.assertEqual(len(list(m.store)), 1)

        m = manager.Manager(
            config.MONGO_URI,
            "explainshell_tests",
            [os.path.join(config.MAN_PAGE_DIR, "1", "tar.1.gz")],
            overwrite=True,
        )
        a, e = m.run()
        self.assertTrue(a)
        self.assertFalse(e)
        self.assertEqual(m.store.mapping.count(), 1)
        self.assertEqual(len(list(m.store)), 1)

        m.store.verify()

    def test_multi_cmd(self):
        m = self._getmanager(["git.1.gz", "git-rebase.1.gz"])
        m.run()

        self.assertTrue(m.store.find_man_page("git")[0].multi_cmd)
        self.assertTrue("git rebase" in m.store)

    def test_edit(self):
        m = self._getmanager(["tar.1.gz"], overwrite=False)
        self.assertEqual(len(list(m.store)), 0)

        a, e = m.run()
        mp = a[0]
        mp.synopsis = "foo"
        m.edit(mp)

        mp = m.store.find_man_page("tar")[0]
        self.assertEqual(mp.synopsis, "foo")
        self.assertTrue(m.store.verify())

        mp.aliases.append(("foo", 1))
        m.edit(mp)
        self.assertTrue("foo" in m.store)
        self.assertEqual(
            m.store.find_man_page("tar")[0].paragraphs,
            m.store.find_man_page("foo")[0].paragraphs,
        )
        self.assertTrue(m.store.verify()[0])

    def test_samename(self):
        pages = [
            os.path.join(config.MAN_PAGE_DIR, "1", "node.1.gz"),
            os.path.join(config.MAN_PAGE_DIR, "8", "node.8.gz"),
        ]
        m = manager.Manager(config.MONGO_URI, "explainshell_tests", pages)
        a, e = m.run()
        self.assertEqual(len(a), 2)
        self.assertEqual(len(m.store.find_man_page("node")), 2)
        mps = m.store.find_man_page("node.8")
        self.assertEqual(len(mps), 2)
        self.assertEqual(mps[0].section, "8")

    def test_samename_samesection(self):
        m = self._getmanager(["xargs.1.gz", "xargs.1posix.gz"])
        a, e = m.run()
        self.assertEqual(len(a), 2)
        self.assertEqual(len(m.store.find_man_page("xargs")), 2)
        mps = m.store.find_man_page("xargs.1posix")
        self.assertEqual(len(mps), 2)
        self.assertEqual(mps[0].section, "1posix")
