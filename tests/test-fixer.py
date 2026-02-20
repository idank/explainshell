import unittest
import copy

from explainshell import fixer, options, store


class test_fixer(unittest.TestCase):
    def setUp(self):
        self._oldfixerscls = fixer.fixers_cls[:]

    def tearDown(self):
        fixer.fixers_cls = self._oldfixerscls

    def test_changes(self):
        class myfixer(fixer.BaseFixer):
            def pre_get_raw_manpage(self):
                self.mctx["foo"] = "bar"

        d = {}
        fixer.fixers_cls = [myfixer]
        r = fixer.Runner(d)
        self.assertTrue("foo" not in d)
        r.pre_get_raw_manpage()
        self.assertEqual(d["foo"], "bar")

    def test_paragraphjoiner(self):
        maxdistance = fixer.ParagraphJoiner.max_distance

        paragraphs = [
            store.Paragraph(i, chr(ord("a") + i), None, False) for i in range(26)
        ]
        options = [
            store.Option(paragraphs[0], [], [], False),
            store.Option(paragraphs[1], [], [], False),
            store.Option(paragraphs[5], [], [], False),
            store.Option(paragraphs[5 + maxdistance - 1], [], [], False),
            store.Option(paragraphs[15], [], [], False),
            store.Option(paragraphs[17], [], [], False),
            store.Option(paragraphs[-1], [], [], False),
        ]

        f = fixer.ParagraphJoiner(None)
        merged = f._join(paragraphs, options)

        # self.assertEqual(merged, 7)
        # self.assertEqual(len(paragraphs), 19)
        self.assertEqual(options[0].text, "a")
        self.assertEqual(options[1].text.replace("\n", ""), "bcde")
        self.assertEqual(options[2].text.replace("\n", ""), "fghi")
        self.assertEqual(options[3].text, "j")
        self.assertEqual(options[4].text.replace("\n", ""), "pq")
        self.assertEqual(options[5].text, "r")
        self.assertEqual(options[6].text, "z")

        # join again to make sure nothing is changed
        oldparagraphs = copy.deepcopy(paragraphs)
        oldoptions = copy.deepcopy(options)
        f._join(paragraphs, options)
        self.assertEqual(oldparagraphs, paragraphs)
        self.assertEqual(oldoptions, options)
