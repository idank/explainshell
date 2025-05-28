import unittest, os, subprocess

from explainshell import manpage, store


class test_manpage(unittest.TestCase):
    def test_first_paragraph_no_section(self):
        m = "foo\nbar"
        l = list(manpage._parse_text(m.splitlines()))
        self.assertEqual(l, [store.Paragraph(0, "foo\nbar", None, False)])

    def test_sections(self):
        m = """<b>SECTION</b>
a
b

c

<b>SECTION2</b>
a

<b>WITH SPACES</b>
a

<b>EMPTY SECTION SHOULD BE IGNORED</b>

<b>SECTION3</b>

tNOTASECTION"""

        parsed = list(manpage._parse_text(m.splitlines()))
        self.assertTrue(len(parsed) == 5)
        self.assertEqual(
            parsed,
            [
                store.Paragraph(0, "a\nb", "SECTION", False),
                store.Paragraph(1, "c", "SECTION", False),
                store.Paragraph(2, "a", "SECTION2", False),
                store.Paragraph(3, "a", "WITH SPACES", False),
                store.Paragraph(4, "tNOTASECTION", "SECTION3", False),
            ],
        )

    def test_no_synopsis(self):
        m = manpage.ManPage("foo")
        m._text = "a b c d e f g h i j k l".replace(" ", "\n")
        m.parse()
        self.assertEqual(m.aliases, [("foo", 10)])
