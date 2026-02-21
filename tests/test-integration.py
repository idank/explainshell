import os
import tempfile
import unittest

from explainshell import manager, config, matcher


@unittest.skip("nltk usage is broken due to new version")
class test_integration(unittest.TestCase):
    def test(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(db_path)
        mngr = manager.Manager(
            db_path,
            [os.path.join(os.path.dirname(__file__), "echo.1.gz")],
            drop=True,
        )
        mngr.run()

        cmd = "echo -en foobar --version"

        m = matcher.Matcher(cmd, mngr.store)
        group = m.match()[1]
        matchprog, matches = group.manpage.name, group.results

        self.assertEqual(matchprog, "echo")

        # self.assertEqual(matches[0].text, 'display a line of text')
        self.assertEqual(matches[0].match, "echo")

        self.assertEqual(
            matches[1].text, "<b>-e</b>     enable interpretation of backslash escapes"
        )
        self.assertEqual(matches[1].match, "-e")

        self.assertEqual(
            matches[2].text, "<b>-n</b>     do not output the trailing newline"
        )
        self.assertEqual(matches[2].match, "n")

        self.assertEqual(matches[3].text, None)
        self.assertEqual(matches[3].match, "foobar")

        self.assertEqual(
            matches[4].text,
            "<b>--version</b>\n       output version information and exit",
        )
        self.assertEqual(matches[4].match, "--version")
