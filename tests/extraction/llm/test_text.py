"""Tests for explainshell.extraction.llm.text — mandoc text processing."""

import os
import re
import unittest
from unittest.mock import MagicMock, patch

from explainshell.errors import ExtractionError
from explainshell.extraction.llm.text import (
    _BLACKLISTED_SECTIONS,
    _MAX_PREAMBLE_CHARS,
    CHUNK_SIZE_CHARS,
    _build_preamble,
    chunk_text,
    clean_mandoc_artifacts,
    filter_sections,
    get_manpage_text,
    number_lines,
)
from tests.helpers import TESTS_DIR

# ---------------------------------------------------------------------------
# TestGetManpageText
# ---------------------------------------------------------------------------


@patch("explainshell.extraction.llm.text.os.path.isfile", return_value=True)
class TestGetManpageText(unittest.TestCase):
    @patch("explainshell.extraction.llm.text.subprocess.run")
    def test_success_returns_markdown(self, mock_run, _mock_isfile):
        md_output = (
            "# NAME\n\ngrep - search for patterns\n\n**-v**, **--invert-match**\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=md_output, stderr="")
        result = get_manpage_text("dummy.1.gz")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("-T", cmd)
        self.assertIn("markdown", cmd)
        self.assertEqual(result, md_output.strip())

    @patch("explainshell.extraction.llm.text.subprocess.run")
    def test_empty_output_raises(self, mock_run, _mock_isfile):
        mock_run.return_value = MagicMock(returncode=0, stdout="   ", stderr="")
        with self.assertRaises(ExtractionError):
            get_manpage_text("dummy.1.gz")

    @patch("explainshell.extraction.llm.text.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run, _mock_isfile):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with self.assertRaises(ExtractionError):
            get_manpage_text("dummy.1.gz")


# ---------------------------------------------------------------------------
# TestGetManpageTextReal — exercises the actual mandoc binary
# ---------------------------------------------------------------------------

_ECHO_GZ = os.path.join(TESTS_DIR, "manpages", "ubuntu", "26.04", "1", "echo.1.gz")
_FIND_GZ = os.path.join(TESTS_DIR, "manpages", "ubuntu", "26.04", "1", "find.1.gz")


class TestGetManpageTextReal(unittest.TestCase):
    def test_mandoc_produces_markdown(self):
        text = get_manpage_text(_ECHO_GZ)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_mandoc_output_contains_option(self):
        text = get_manpage_text(_ECHO_GZ)
        self.assertIn("-n", text)

    def test_mandoc_output_has_markdown_formatting(self):
        text = get_manpage_text(_ECHO_GZ)
        # Should contain some markdown bold or italic markers
        self.assertTrue(
            "**" in text or "*" in text,
            f"Expected markdown formatting in output, got: {text[:300]}",
        )

    def test_no_non_breaking_spaces(self):
        """mandoc emits &#x00A0; between flags and args; these should be regular spaces."""
        text = get_manpage_text(_FIND_GZ)
        self.assertNotIn("\xa0", text)

    def test_zwnj_entities_preserved(self):
        """clean_mandoc_artifacts preserves &zwnj; — mandoc inserts it between
        abutting bold/italic spans so CommonMark parses them as separate runs.
        Stripping it produced garbled emphasis (e.g. **--config-file=*****file*)
        in stored option text. See the function's docstring for context.
        """
        raw = "**foo**&zwnj;**bar**"
        cleaned = clean_mandoc_artifacts(raw)
        self.assertIn("&zwnj;", cleaned)

    def test_no_nbsp_entities(self):
        """clean_mandoc_artifacts replaces &nbsp; entities with plain spaces."""
        raw = get_manpage_text(_FIND_GZ)
        cleaned = clean_mandoc_artifacts(raw)
        self.assertNotIn("&nbsp;", cleaned)

    def test_no_artificial_line_wrapping(self):
        """Prose paragraphs should not be hard-wrapped at terminal width (~78 cols)."""
        text = get_manpage_text(_FIND_GZ)
        content_lines = [
            line
            for line in text.split("\n")
            if line and not line.startswith("=") and not line.startswith("[")
        ]
        long_lines = [line for line in content_lines if len(line) > 80]
        self.assertGreater(
            len(long_lines),
            0,
            "Expected long prose lines (>80 chars) but all lines were short — "
            "mandoc whitespace may be leaking through as hard wraps",
        )


# ---------------------------------------------------------------------------
# TestChunkText
# ---------------------------------------------------------------------------


class TestChunkText(unittest.TestCase):
    def test_small_text_no_split(self):
        text = "hello world\n\nfoo bar"
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        # Should contain numbered lines
        self.assertIn("1| hello world", chunks[0])
        self.assertIn("3| foo bar", chunks[0])

    def test_large_text_splits_on_sections(self):
        # Build text with multiple sections, large enough to exceed CHUNK_SIZE_CHARS
        sections = []
        for i in range(10):
            body = ("x" * 80 + "\n") * 100  # ~8100 chars per section
            sections.append(f"## Section {i}\n\n{body}")
        text = "# NAME\n\ntest\n\n" + "\n".join(sections)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)

    def test_oversized_section_with_paragraphs_splits(self):
        # Build one big section with paragraph breaks
        paras = ["z" * 5000 for _ in range(20)]
        text = "# BIG\n\n" + "\n\n".join(paras)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), CHUNK_SIZE_CHARS)

    def test_single_unsplittable_line(self):
        # A single line with no breaks can't be split further — it stays as one oversized chunk
        text = "z" * (CHUNK_SIZE_CHARS + 1)
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0]), CHUNK_SIZE_CHARS)

    def test_line_numbers_are_globally_correct(self):
        # Build text that will split into 2 chunks
        section_body = ("line\n") * 800  # ~4000 chars per section
        sections = []
        for i in range(20):
            sections.append(f"## Section {i}\n\n{section_body}")
        text = "# NAME\n\ntest\n\n" + "\n".join(sections)
        chunks = chunk_text(text)
        self.assertGreaterEqual(len(chunks), 2)

        # Last numbered line in chunk 0 + 1 == first numbered line in chunk 1
        last_match = list(re.finditer(r"^\s*(\d+)\|", chunks[0], re.MULTILINE))
        first_match = list(re.finditer(r"^\s*(\d+)\|", chunks[1], re.MULTILINE))
        self.assertTrue(last_match)
        self.assertTrue(first_match)
        last_line = int(last_match[-1].group(1))
        first_line = int(first_match[0].group(1))
        self.assertEqual(first_line, last_line + 1)

    def test_preamble_on_later_chunks(self):
        section_body = ("line\n") * 800
        sections = []
        for i in range(20):
            sections.append(f"## Section {i}\n\n{section_body}")
        text = "# NAME\n\ntest-cmd\n\n# SYNOPSIS\n\ntest-cmd [opts]\n\n" + "\n".join(
            sections
        )
        chunks = chunk_text(text)
        if len(chunks) >= 2:
            self.assertIn("[Context", chunks[1])
            self.assertIn("test-cmd", chunks[1])

    def test_oversized_preamble_does_not_explode_chunks(self):
        """A manpage whose NAME section exceeds CHUNK_SIZE_CHARS should not
        produce one chunk per line (the bwbasic bug)."""
        # Build a manpage where # NAME alone is larger than CHUNK_SIZE_CHARS.
        # The content is one long line so it can't be split further.
        huge_name = "x " * (CHUNK_SIZE_CHARS + 5000)
        options_body = ("opt line\n") * 200
        text = f"# NAME\n\n{huge_name}\n\n# OPTIONS\n\n{options_body}"
        chunks = chunk_text(text)
        # Without the preamble cap this would produce hundreds of chunks;
        # with the fix it should be a small, bounded number.
        self.assertLess(
            len(chunks),
            20,
            f"Expected few chunks but got {len(chunks)} — preamble cap may not be working",
        )


# ---------------------------------------------------------------------------
# TestChunkBoundaryIntegrity
# ---------------------------------------------------------------------------


# A numbered line holding nothing but an option heading, e.g. "3492| **--foo**".
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+\|\s*\*\*[-+]")


class TestChunkBoundaryIntegrity(unittest.TestCase):
    """A chunk must never end on an option heading split from its description.

    The extraction prompt tells the model to extract options only from their
    *defining* documentation. A heading stranded at the end of one chunk, with
    its body at the start of the next, is therefore dropped by both: the first
    chunk sees a name with no description, the second a description with no
    name. See ``_is_bare_option_heading`` in the chunker.
    """

    @staticmethod
    def _manpage(n_options: int, body_chars: int, headings_per_option: int = 1) -> str:
        """A synthetic man page of `heading(s) + body` option blocks."""
        parts = ["# NAME", "", "testcmd - a test command", "", "# OPTIONS", ""]
        for i in range(n_options):
            for h in range(headings_per_option):
                parts.append(f"**--option-{i}-{h}**")
                parts.append("")
            parts.append("> " + "x" * body_chars)
            parts.append("")
        return "\n".join(parts)

    @staticmethod
    def _numbered(chunk: str) -> list[tuple[int, str]]:
        return [
            (int(m.group(1)), m.group(2))
            for m in re.finditer(r"^\s*(\d+)\|(.*)$", chunk, re.MULTILINE)
        ]

    def _assert_no_orphan_heading(self, chunks: list[str], msg: str) -> None:
        for i, chunk in enumerate(chunks):
            lines = [ln for ln in chunk.strip().splitlines() if ln.strip()]
            if not lines:
                continue
            self.assertIsNone(
                _NUMBERED_HEADING_RE.match(lines[-1]),
                f"{msg}: chunk {i} ends on an orphaned option heading: {lines[-1]!r}",
            )

    def _assert_lines_intact(self, text: str, chunks: list[str], msg: str) -> None:
        """Every numbered line must match the source, exactly once, in order."""
        original = text.split("\n")
        seen: dict[int, int] = {}
        for i, chunk in enumerate(chunks):
            previous = 0
            for n, content in self._numbered(chunk):
                self.assertTrue(
                    1 <= n <= len(original), f"{msg}: line {n} out of range"
                )
                self.assertEqual(
                    content.strip(),
                    original[n - 1].strip(),
                    f"{msg}: line {n} does not match the source",
                )
                self.assertGreater(n, previous, f"{msg}: line {n} out of order")
                previous = n
                self.assertNotIn(n, seen, f"{msg}: line {n} duplicated across chunks")
                seen[n] = i
        for n, line in enumerate(original, start=1):
            if line.strip():
                self.assertIn(n, seen, f"{msg}: line {n} missing from every chunk")

    def test_headings_never_stranded_at_chunk_end(self):
        # Vary the body size so boundaries land at many different offsets;
        # at some of these a boundary falls right after an option heading.
        for body_chars in (500, 900, 1500, 2000, 3000):
            with self.subTest(body_chars=body_chars):
                text = self._manpage(n_options=120, body_chars=body_chars)
                chunks = chunk_text(text)
                self.assertGreater(len(chunks), 1)
                msg = f"body_chars={body_chars}"
                self._assert_no_orphan_heading(chunks, msg)
                self._assert_lines_intact(text, chunks, msg)

    def test_run_of_consecutive_headings_stays_with_its_body(self):
        """Several headings may share one description (e.g. gcc's -mfoo/-mno-foo).

        The whole run has to travel to the next chunk, not just the last one.
        """
        for headings in (2, 3):
            for body_chars in (500, 900, 1500, 2000, 3000):
                with self.subTest(headings=headings, body_chars=body_chars):
                    text = self._manpage(
                        n_options=120,
                        body_chars=body_chars,
                        headings_per_option=headings,
                    )
                    chunks = chunk_text(text)
                    self.assertGreater(len(chunks), 1)
                    msg = f"headings={headings} body_chars={body_chars}"
                    self._assert_no_orphan_heading(chunks, msg)
                    self._assert_lines_intact(text, chunks, msg)

    def test_carrying_a_heading_does_not_add_chunks(self):
        """Moving a heading forward must not cost an extra chunk per boundary."""
        text = self._manpage(n_options=120, body_chars=1500)
        chunks = chunk_text(text)
        total = len("\n".join(chunks))
        # A sane packing keeps chunk count near the size-implied minimum.
        self.assertLessEqual(len(chunks), total // CHUNK_SIZE_CHARS + 2)


# ---------------------------------------------------------------------------
# TestBuildPreamble
# ---------------------------------------------------------------------------


class TestBuildPreamble(unittest.TestCase):
    def test_normal_preamble(self):
        text = (
            "# NAME\n\nfoo - a tool\n\n# SYNOPSIS\n\nfoo [opts]\n\n# OPTIONS\n\n**-v**"
        )
        preamble = _build_preamble(text)
        self.assertIn("foo - a tool", preamble)
        self.assertIn("foo [opts]", preamble)
        # OPTIONS should not be in preamble
        self.assertNotIn("-v", preamble)

    def test_description_only_first_paragraph(self):
        text = (
            "# NAME\n\nfoo\n\n"
            "# DESCRIPTION\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird."
        )
        preamble = _build_preamble(text)
        self.assertIn("First paragraph.", preamble)
        self.assertNotIn("Third.", preamble)

    def test_oversized_name_section_is_truncated(self):
        huge_name = "word " * (_MAX_PREAMBLE_CHARS // 3)
        text = f"# NAME\n\n{huge_name}\n\n# OPTIONS\n\n**-v**"
        preamble = _build_preamble(text)
        self.assertLessEqual(len(preamble), _MAX_PREAMBLE_CHARS + 50)
        self.assertTrue(preamble.endswith("[…truncated]"))

    def test_truncation_at_line_boundary(self):
        """Truncation should not cut in the middle of a line."""
        lines = [f"line {i} " + "x" * 100 for i in range(_MAX_PREAMBLE_CHARS // 100)]
        huge_name = "\n".join(lines)
        text = f"# NAME\n\n{huge_name}\n\n# OPTIONS\n\n**-v**"
        preamble = _build_preamble(text)
        # The last real content line (before the marker) should be complete
        preamble_lines = preamble.split("\n")
        self.assertEqual(preamble_lines[-1], "[…truncated]")
        # The second-to-last line should be one of our generated lines
        self.assertTrue(preamble_lines[-2].startswith("line "))

    def test_no_preamble_sections(self):
        text = "# OPTIONS\n\n**-v** verbose\n\n# EXAMPLES\n\nfoo -v"
        preamble = _build_preamble(text)
        self.assertEqual(preamble, "")


# ---------------------------------------------------------------------------
# TestNumberLines
# ---------------------------------------------------------------------------


class TestNumberLines(unittest.TestCase):
    def test_basic(self):
        text = "alpha\nbeta\ngamma"
        numbered, orig = number_lines(text)
        self.assertEqual(orig[1], "alpha")
        self.assertEqual(orig[2], "beta")
        self.assertEqual(orig[3], "gamma")
        self.assertIn("1| alpha", numbered)
        self.assertIn("2| beta", numbered)
        self.assertIn("3| gamma", numbered)

    def test_single_line(self):
        numbered, orig = number_lines("hello")
        self.assertEqual(orig[1], "hello")
        self.assertEqual(numbered, "1| hello")

    def test_preserves_empty_lines(self):
        text = "a\n\nb"
        _numbered, orig = number_lines(text)
        self.assertEqual(orig[1], "a")
        self.assertEqual(orig[2], "")
        self.assertEqual(orig[3], "b")

    def test_line_number_padding(self):
        # 100 lines -> 3-digit width
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        numbered, _orig = number_lines(text)
        first_line = numbered.split("\n")[0]
        # "  1| line0" — padded to 3 digits
        self.assertTrue(first_line.startswith("  1| "))


# ---------------------------------------------------------------------------
# TestFilterSections
# ---------------------------------------------------------------------------


class TestFilterSections(unittest.TestCase):
    def test_removes_blacklisted_sections(self):
        text = (
            "# NAME\n\nfoo - a tool\n\n"
            "# OPTIONS\n\n**-v**\n\n> verbose\n\n"
            "# SEE ALSO\n\nbar(1)\n\n"
            "# BUGS\n\nNone known."
        )
        filtered, counts = filter_sections(text)
        self.assertIn("# NAME", filtered)
        self.assertIn("# OPTIONS", filtered)
        self.assertNotIn("# SEE ALSO", filtered)
        self.assertNotIn("bar(1)", filtered)
        self.assertNotIn("# BUGS", filtered)
        self.assertEqual(counts, {"SEE ALSO": 1, "BUGS": 1})

    def test_keeps_non_blacklisted_sections(self):
        text = "# NAME\n\nfoo\n\n# DESCRIPTION\n\nSome desc\n\n# OPTIONS\n\n**-v**"
        filtered, counts = filter_sections(text)
        self.assertEqual(filtered, text)
        self.assertEqual(counts, {})

    def test_removes_subsections_under_blacklisted_top_level(self):
        text = (
            "# NAME\n\nfoo\n\n"
            "# BUGS\n\nSome bugs\n\n"
            "## Known Issues\n\nIssue 1\n\n"
            "# OPTIONS\n\n**-v**"
        )
        filtered, _counts = filter_sections(text)
        self.assertNotIn("BUGS", filtered)
        self.assertNotIn("Known Issues", filtered)
        self.assertIn("# OPTIONS", filtered)

    def test_case_insensitive_matching(self):
        # mandoc typically outputs uppercase, but test robustness
        text = "# NAME\n\nfoo\n\n# Copyright\n\n2024 Foo Inc."
        filtered, counts = filter_sections(text)
        self.assertNotIn("Copyright", filtered)
        self.assertEqual(counts, {"COPYRIGHT": 1})

    def test_does_not_remove_subsections_with_blacklisted_name(self):
        """## AUTHORS under a non-blacklisted parent should not be removed."""
        text = (
            "# NAME\n\nfoo\n\n"
            "# ACKNOWLEDGEMENTS\n\nThanks to:\n\n"
            "## AUTHORS\n\nJohn Doe\n\n"
            "# OPTIONS\n\n**-v**"
        )
        filtered, counts = filter_sections(text)
        # ## AUTHORS is a sub-section; only top-level # AUTHORS is blacklisted
        self.assertIn("## AUTHORS", filtered)
        self.assertEqual(counts, {})

    def test_empty_text(self):
        filtered, counts = filter_sections("")
        self.assertEqual(filtered, "")
        self.assertEqual(counts, {})

    def test_all_blacklisted_headings_are_uppercase(self):
        for heading in _BLACKLISTED_SECTIONS:
            self.assertEqual(heading, heading.upper())

    def test_real_manpage_filters(self):
        """Smoke test on a real manpage to verify sections are removed."""
        text = get_manpage_text(_FIND_GZ)
        filtered, counts = filter_sections(text)
        self.assertGreater(len(counts), 0, "Expected some sections to be filtered")
        self.assertLess(len(filtered), len(text))
        # Core sections should survive
        self.assertIn("# NAME", filtered)
        self.assertIn("# OPTIONS", filtered)


if __name__ == "__main__":
    unittest.main()
