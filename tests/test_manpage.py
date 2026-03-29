import unittest

from explainshell import manpage


class test_manpage(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            manpage.get_synopsis_and_aliases("foo.1.gz")
