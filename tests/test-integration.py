import unittest, subprocess, pymongo, os

from explainshell import manager, matcher

class test_integration(unittest.TestCase):
    def test(self):
        mngr = manager.manager('localhost', 'explainshell_tests', [os.path.join(os.path.dirname(__file__), 'echo.1.gz')], drop=True)
        mngr.run()

        cmd = 'echo -en foobar --version'

        m = matcher.matcher(cmd, mngr.store)
        matchprog, matches = m.match()[0]

        self.assertEquals(matchprog, 'echo')

        #self.assertEquals(matches[0].text, 'display a line of text')
        self.assertEquals(matches[0].match, 'echo')

        self.assertEquals(matches[1].text, '<b>-e</b>     enable interpretation of backslash escapes')
        self.assertEquals(matches[1].match, '-e')

        self.assertEquals(matches[2].text, '<b>-n</b>     do not output the trailing newline')
        self.assertEquals(matches[2].match, 'n')

        self.assertEquals(matches[3].text, None)
        self.assertEquals(matches[3].match, 'foobar')

        self.assertEquals(matches[4].text, '<b>--version</b>\n       output version information and exit')
        self.assertEquals(matches[4].match, '--version')
