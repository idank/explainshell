"""Text preparation for LLM extraction: mandoc → markdown, section filtering,
line numbering, and chunking."""

from __future__ import annotations

import os
import re
import subprocess

from explainshell.errors import ExtractionError

CHUNK_SIZE_CHARS = 60_000
MAX_MANPAGE_CHARS = 500_000

# Top-level sections that almost never contain option documentation.
_BLACKLISTED_SECTIONS = frozenset(
    s.upper()
    for s in [
        "AUTHOR",
        "AUTHORS",
        "AVAILABILITY",
        "BUGS",
        "COLOPHON",
        "COPYING",
        "COPYRIGHT",
        "COPYRIGHT AND LICENSE",
        "EXIT STATUS",
        "HISTORY",
        "LICENSE",
        "REPORTING BUGS",
        "SEE ALSO",
        "VERSION",
    ]
)


_MANDOC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "tools",
    "mandoc-with-markdown",
)


def get_manpage_text(gz_path: str) -> str:
    """Run patched ``mandoc -T markdown <gz_path>`` to get markdown directly."""
    result = subprocess.run(
        [_MANDOC_PATH, "-T", "markdown", gz_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ExtractionError(f"mandoc failed for {gz_path}: {result.stderr}")
    return result.stdout.strip()


# Keep backward-compatible alias
get_plain_text = get_manpage_text


def _split_sections(text: str) -> list[tuple[int, str]]:
    """Split text into sections at markdown header lines (# or ##).

    Returns a list of (start_line, section_text) tuples where start_line
    is the 1-indexed line number of the first line in the section.
    """
    lines = text.split("\n")
    sections: list[tuple[int, str]] = []
    current_start = 1
    current_lines: list[str] = []

    for i, line in enumerate(lines):
        if re.match(r"^#{1,2} ", line) and current_lines:
            sections.append((current_start, "\n".join(current_lines)))
            current_start = i + 1
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_start, "\n".join(current_lines)))

    return sections


def filter_sections(text: str) -> tuple[str, dict[str, int]]:
    """Remove blacklisted top-level sections from manpage text.

    Returns (filtered_text, removal_counts).
    """
    sections = _split_sections(text)
    kept: list[str] = []
    removal_counts: dict[str, int] = {}
    skip_until_top = False

    for _start, section_text in sections:
        header_line = section_text.split("\n", 1)[0].strip()

        is_top_level = header_line.startswith("# ") and not header_line.startswith(
            "## "
        )

        if is_top_level:
            heading_name = header_line.split(" ", 1)[1].strip().upper()
            if heading_name in _BLACKLISTED_SECTIONS:
                removal_counts[heading_name] = removal_counts.get(heading_name, 0) + 1
                skip_until_top = True
                continue
            else:
                skip_until_top = False

        if skip_until_top:
            continue

        kept.append(section_text)

    return "\n".join(kept), removal_counts


def _build_preamble(text: str) -> str:
    """Extract NAME + SYNOPSIS + first paragraph of DESCRIPTION as preamble."""
    sections = _split_sections(text)
    preamble_headers = {"# NAME", "# SYNOPSIS", "# DESCRIPTION"}
    parts: list[str] = []
    for _start, section_text in sections:
        header_line = section_text.split("\n", 1)[0].strip()
        if header_line in preamble_headers:
            if header_line == "# DESCRIPTION":
                paras = section_text.split("\n\n", 2)
                parts.append("\n\n".join(paras[:2]))
            else:
                parts.append(section_text)
    return "\n\n".join(parts)


def number_lines(text: str) -> tuple[str, dict[int, str]]:
    """Add line numbers to every line of text.

    Returns (numbered_text, original_lines) where original_lines is a
    dict mapping 1-indexed line numbers to their original content.
    """
    lines = text.split("\n")
    original_lines: dict[int, str] = {}
    numbered: list[str] = []
    width = len(str(len(lines)))
    for i, line in enumerate(lines, 1):
        original_lines[i] = line
        numbered.append(f"{i:>{width}}| {line}")
    return "\n".join(numbered), original_lines


def chunk_text(text: str) -> list[str]:
    """Split text into numbered chunks at section boundaries.

    Each chunk gets line numbers corresponding to the original text.
    If the text is small enough, returns a single chunk.
    A preamble (NAME/SYNOPSIS/DESCRIPTION intro) is prepended to each
    chunk beyond the first so the model has context.

    When a single section exceeds the chunk size, it is sub-split at
    paragraph (blank-line) boundaries.
    """
    numbered_full, _ = number_lines(text)
    if len(numbered_full) <= CHUNK_SIZE_CHARS:
        return [numbered_full]

    sections = _split_sections(text)
    preamble = _build_preamble(text)
    preamble_text = ""
    if preamble:
        preamble_text = (
            "[Context — this is a continuation of the same man page]\n\n"
            + preamble
            + "\n\n---\n\n"
        )
    budget = CHUNK_SIZE_CHARS - len(preamble_text)

    total_lines = text.count("\n") + 1
    width = len(str(total_lines))

    def _number_block(start_line: int, block_text: str) -> str:
        lines = block_text.split("\n")
        numbered = []
        for j, line in enumerate(lines):
            lineno = start_line + j
            numbered.append(f"{lineno:>{width}}| {line}")
        return "\n".join(numbered)

    def _split_by_lines(start_line: int, block_text: str) -> list[tuple[int, str]]:
        """Last-resort split: cut at line boundaries to fit budget."""
        lines = block_text.split("\n")
        result: list[tuple[int, str]] = []
        cur_lines: list[str] = []
        cur_start = start_line
        for line in lines:
            candidate = "\n".join(cur_lines + [line])
            if len(_number_block(cur_start, candidate)) > budget and cur_lines:
                result.append((cur_start, "\n".join(cur_lines)))
                cur_start += len(cur_lines)
                cur_lines = []
            cur_lines.append(line)
        if cur_lines:
            result.append((cur_start, "\n".join(cur_lines)))
        return result

    blocks: list[tuple[int, str]] = []
    for start_line, section_text in sections:
        numbered = _number_block(start_line, section_text)
        if len(numbered) <= budget:
            blocks.append((start_line, section_text))
        else:
            paragraphs = section_text.split("\n\n")
            cur_paras: list[str] = []
            cur_start = start_line
            for para in paragraphs:
                candidate = "\n\n".join(cur_paras + [para])
                numbered_candidate = _number_block(cur_start, candidate)
                if len(numbered_candidate) > budget and cur_paras:
                    blocks.append((cur_start, "\n\n".join(cur_paras)))
                    cur_start = cur_start + "\n\n".join(cur_paras).count("\n") + 2
                    cur_paras = []
                cur_paras.append(para)
            if cur_paras:
                blocks.append((cur_start, "\n\n".join(cur_paras)))

    final_blocks: list[tuple[int, str]] = []
    for start_line, block_text in blocks:
        if len(_number_block(start_line, block_text)) > budget:
            final_blocks.extend(_split_by_lines(start_line, block_text))
        else:
            final_blocks.append((start_line, block_text))
    blocks = final_blocks

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for start_line, block_text in blocks:
        numbered_block = _number_block(start_line, block_text)
        block_len = len(numbered_block) + 1

        if current_len + block_len > budget and current_parts:
            chunks.append("\n".join(current_parts))
            current_parts = []
            current_len = 0

        current_parts.append(numbered_block)
        current_len += block_len

    if current_parts:
        chunks.append("\n".join(current_parts))

    if len(chunks) > 1 and preamble_text:
        for i in range(1, len(chunks)):
            chunks[i] = preamble_text + chunks[i]

    return chunks
