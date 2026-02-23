import unittest

from explainshell import manpage


class test_manpage(unittest.TestCase):
    def test_no_synopsis(self):
        synopsis, aliases = manpage.get_synopsis_and_aliases("foo.1.gz")
        # Without lexgrog available, we get no synopsis and just the name alias
        self.assertIsNone(synopsis)
        self.assertEqual(aliases, [("foo", 10)])
