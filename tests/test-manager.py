import os
import tempfile
import unittest

from explainshell import legacy_manager as manager, config, store, errors


@unittest.skip("nltk usage is broken due to new version")
class test_manager(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # let Store create it fresh

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _getmanager(self, names, **kwargs):
        paths = []
        for n in names:
            paths.append(os.path.join(config.MAN_PAGE_DIR, "1", n))

        m = manager.Manager(self.db_path, paths, **kwargs)
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
        m.run()

        # Temporarily disable FK enforcement to insert a dangling mapping.
        # In production this is prevented by ON DELETE CASCADE, but verify()
        # must still detect it gracefully.
        s._conn.execute("PRAGMA foreign_keys = OFF")
        s._conn.execute("INSERT INTO mapping(src, dst, score) VALUES ('foo', 9999, 1)")
        s._conn.commit()
        s._conn.execute("PRAGMA foreign_keys = ON")
        ok, unreachable, notfound = s.verify()
        self.assertFalse(ok)
        self.assertIn(9999, notfound)

        s._conn.execute("DELETE FROM mapping")
        s._conn.commit()
        ok, unreachable, notfound = s.verify()
        self.assertFalse(ok)
        self.assertIn("tar", unreachable)

        s._conn.execute("PRAGMA foreign_keys = OFF")
        s._conn.execute("INSERT INTO mapping(src, dst, score) VALUES ('foo', 9999, 1)")
        s._conn.commit()
        s._conn.execute("PRAGMA foreign_keys = ON")
        ok, unreachable, notfound = s.verify()
        self.assertIn("tar", unreachable)
        self.assertIn(9999, notfound)

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
        self.assertEqual(
            self._count_mappings(m.store), 1
        )
        self.assertEqual(len(list(m.store)), 1)

        a, e = m.run()
        self.assertFalse(a)
        self.assertTrue(e)
        self.assertEqual(self._count_mappings(m.store), 1)
        self.assertEqual(len(list(m.store)), 1)

        m = manager.Manager(
            self.db_path,
            [os.path.join(config.MAN_PAGE_DIR, "1", "tar.1.gz")],
            overwrite=True,
        )
        a, e = m.run()
        self.assertTrue(a)
        self.assertFalse(e)
        self.assertEqual(self._count_mappings(m.store), 1)
        self.assertEqual(len(list(m.store)), 1)

        m.store.verify()

    def _count_mappings(self, s):
        return s._conn.execute("SELECT COUNT(*) FROM mapping").fetchone()[0]

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
        m = manager.Manager(self.db_path, pages)
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
