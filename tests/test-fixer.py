import unittest
import copy

from explainshell import fixer, options, store

class test_fixer(unittest.TestCase):
    def setUp(self):
        self._oldfixerscls = fixer.fixerscls[:]

    def tearDown(self):
        fixer.fixerscls = self._oldfixerscls

    def test_changes(self):
        class myfixer(fixer.basefixer):
            def pre_get_raw_manpage(self):
                self.mctx['foo'] = 'bar'

        d = {}
        fixer.fixerscls = [myfixer]
        r = fixer.runner(d)
        self.assertTrue('foo' not in d)
        r.pre_get_raw_manpage()
        self.assertEquals(d['foo'], 'bar')

    def test_paragraphjoiner(self):
        maxdistance = fixer.paragraphjoiner.maxdistance

        paragraphs = [store.paragraph(i, chr(ord('a') + i), None, False) for i in range(26)]
        options = [
                store.option(paragraphs[0], [], [], False),
                store.option(paragraphs[1], [], [], False),
                store.option(paragraphs[5], [], [], False),
                store.option(paragraphs[5+maxdistance-1], [], [], False),
                store.option(paragraphs[15], [], [], False),
                store.option(paragraphs[17], [], [], False),
                store.option(paragraphs[-1], [], [], False)]

        f = fixer.paragraphjoiner(None)
        merged = f._join(paragraphs, options)

        #self.assertEquals(merged, 7)
        #self.assertEquals(len(paragraphs), 19)
        self.assertEquals(options[0].text, 'a')
        self.assertEquals(options[1].text.replace('\n', ''), 'bcde')
        self.assertEquals(options[2].text.replace('\n', ''), 'fghi')
        self.assertEquals(options[3].text, 'j')
        self.assertEquals(options[4].text.replace('\n', ''), 'pq')
        self.assertEquals(options[5].text, 'r')
        self.assertEquals(options[6].text, 'z')

        # join again to make sure nothing is changed
        oldparagraphs = copy.deepcopy(paragraphs)
        oldoptions = copy.deepcopy(options)
        f._join(paragraphs, options)
        self.assertEquals(oldparagraphs, paragraphs)
        self.assertEquals(oldoptions, options)
