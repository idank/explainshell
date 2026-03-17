"""
Parse mandoc -T tree output into a Python tree, then extract options.

mandoc already handles all roff complexity (dialect detection, macro
expansion, escape sequences, nesting).  This module walks the resulting
AST instead of pattern-matching raw roff lines.

Public API:
    parse_options(gz_path) -> ExtractionResult
    assess_confidence(result) -> ConfidenceResult
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field

from explainshell import models
from explainshell.roff_parser import clean_roff, _parse_flag_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Result of tree-based option extraction with diagnostic metadata."""

    options: list = field(default_factory=list)  # list[models.Option]
    tree_text: str = ""
    mandoc_stderr: str = ""
    option_sections_found: int = 0  # sections matching _is_option_section()
    option_sections_empty: int = 0  # matched sections that yielded 0 options
    total_body_children: int = 0  # children traversed in option sections
    unrecognized_children: int = 0  # children that fell through to else: i += 1
    empty_description_count: int = 0
    is_mdoc: bool = False


@dataclass
class ConfidenceResult:
    """Whether the tree parser is confident in its extraction."""

    confident: bool = True
    reasons: list = field(default_factory=list)  # list[str]

    def __str__(self):
        if self.confident:
            return "confident"
        return f"not confident: {'; '.join(self.reasons)}"


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tree data structure
# ---------------------------------------------------------------------------


class Node:
    """A node in the mandoc parse tree."""

    __slots__ = ("kind", "subkind", "text", "attrs", "children")

    def __init__(self, kind: str, subkind: str = "", text: str = "", attrs: str = ""):
        self.kind = kind  # "SH", "TP", "IP", "B", "Bl", "It", ...
        self.subkind = subkind  # "block", "head", "body", "elem", "text"
        self.text = text  # text content for text/elem nodes
        self.attrs = attrs  # trailing attributes (e.g. "-tag -width Ds")
        self.children: list[Node] = []

    def __repr__(self):
        parts = [self.kind]
        if self.subkind:
            parts.append(f"({self.subkind})")
        if self.text:
            parts.append(repr(self.text[:40]))
        return f"<Node {' '.join(parts)}>"

    def find(self, kind: str, subkind: str = "") -> "list[Node]":
        """Find direct children matching kind (and optionally subkind)."""
        return [
            c
            for c in self.children
            if c.kind == kind and (not subkind or c.subkind == subkind)
        ]

    def find_recursive(self, kind: str, subkind: str = "") -> "list[Node]":
        """Find all descendants matching kind (and optionally subkind)."""
        result = []
        for c in self.children:
            if c.kind == kind and (not subkind or c.subkind == subkind):
                result.append(c)
            result.extend(c.find_recursive(kind, subkind))
        return result

    def get_text(self) -> str:
        """Recursively collect all text content from this node."""
        parts = []
        self._collect_text(parts)
        return _smart_join(parts)

    def _collect_text(self, parts: list):
        if self.subkind == "text" and self.text:
            parts.append(self.text)
        for c in self.children:
            c._collect_text(parts)

    def get_head(self) -> "Node | None":
        """Get the 'head' child of a block node."""
        heads = self.find(self.kind, "head")
        return heads[0] if heads else None

    def get_body(self) -> "Node | None":
        """Get the 'body' child of a block node."""
        bodies = self.find(self.kind, "body")
        return bodies[0] if bodies else None


# Characters that should not have a space inserted before them when joining
# adjacent text fragments from alternating macros (BI, BR, etc.).
_NO_SPACE_BEFORE = frozenset(",.;:=)]")
# Characters that should not have a space inserted after them.
_NO_SPACE_AFTER = frozenset("=([")


def _smart_join(parts: list[str]) -> str:
    """Join text parts, suppressing spaces around punctuation and '='."""
    if not parts:
        return ""
    result = parts[0]
    for p in parts[1:]:
        if not p:
            continue
        if not result:
            result = p
            continue
        if result[-1] in _NO_SPACE_AFTER or p[0] in _NO_SPACE_BEFORE:
            result += p
        else:
            result += " " + p
    return result


# ---------------------------------------------------------------------------
# Parse mandoc -T tree output into Node tree
# ---------------------------------------------------------------------------

# Matches lines like:
#   SH (block) *2:2
#   SH (head) 2:2 ID=HREF
#       NAME (text) 2:5
#   B (elem) *5:2
#   BI (elem) *79:2
#       \-a  (text) 79:5
#   Bl (block) -tag -width Ds *125:2
# Note: source position may have trailing '.' (sentence-end marker)
# Split into two simpler regexes + a pre-filter for speed (2x faster than
# a single alternation regex).
_TEXT_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<text>.+?)"
    r"\s+\(text\)"
    r"(?:\s+\*?\d+:\d+\.?)"
    r"(?:\s+\S+)*"
    r"\s*$"
)
_STRUCT_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<name>\w+)"
    r"\s+"
    r"\((?P<subkind>block|head|body|elem)\)"
    r"(?P<attrs>(?:\s+(?!\*?\d+:\d+)\S+)*)"
    r"(?:\s+\*?\d+:\d+\.?)"
    r"(?:\s+\S+)*"
    r"\s*$"
)

# Simpler: match a (comment) line — skip these
_COMMENT_RE = re.compile(r"^\s+.+\(comment\)\s+\d+:\d+\s*$")

# Header lines: title = "XARGS", etc.
_HEADER_RE = re.compile(r"^(?:title|name|sec|vol|os|date)\s+=")

# Detect lines that have a mandoc tree node marker.
# The position may not be immediately after the subkind (attrs can intervene).
_HAS_MARKER_RE = re.compile(r"\((?:text|block|head|body|elem|comment)\)\s+")


def _join_wrapped_lines(text: str) -> str:
    """Join continuation lines in mandoc -T tree output.

    mandoc wraps long text nodes across lines.  A continuation line
    lacks the (text)/(block)/etc. marker.  We accumulate such lines
    and prepend them to the next line that has a marker.
    """
    lines = text.splitlines()
    joined = []
    pending = ""

    for line in lines:
        if not line.strip():
            if pending:
                joined.append(pending)
                pending = ""
            joined.append(line)
            continue

        if _HAS_MARKER_RE.search(line) or _HEADER_RE.match(line):
            if pending:
                # Prepend accumulated continuation to this marker line
                joined.append(pending + line.lstrip())
                pending = ""
            else:
                joined.append(line)
        else:
            # No marker — accumulate as continuation
            if pending:
                pending += line.lstrip()
            else:
                pending = line

    if pending:
        joined.append(pending)

    return "\n".join(joined)


def parse_tree(tree_text: str) -> Node:
    """Parse mandoc -T tree output into a Node tree.

    Returns a root Node whose children are the top-level sections.
    """
    tree_text = _join_wrapped_lines(tree_text)

    root = Node("root", "block")
    stack: list[tuple[int, Node]] = [(-1, root)]  # (indent_level, node)

    for line in tree_text.splitlines():
        # Skip header lines, comments, blank lines
        if not line.strip():
            continue
        if _HEADER_RE.match(line):
            continue
        if _COMMENT_RE.match(line):
            continue

        # Pre-filter: dispatch to the right regex based on whether
        # the line contains "(text)".  This avoids the cost of a
        # single alternation regex trying both branches.
        if "(text)" in line:
            m = _TEXT_RE.match(line)
            if not m:
                continue
            indent = len(m.group("indent"))
            text_content = m.group("text").strip()
            node = Node("text", "text", text=text_content)
        else:
            m = _STRUCT_RE.match(line)
            if not m:
                continue
            indent = len(m.group("indent"))
            name = m.group("name")
            subkind = m.group("subkind")
            attrs = (m.group("attrs") or "").strip()
            node = Node(name, subkind, attrs=attrs)

        # Pop stack until we find a parent with smaller indent
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if stack:
            stack[-1][1].children.append(node)

        stack.append((indent, node))

    return root


_MANDOC_ENV = None


def _get_mandoc_env():
    global _MANDOC_ENV
    if _MANDOC_ENV is None:
        _MANDOC_ENV = {**os.environ, "MANWIDTH": "10000"}
    return _MANDOC_ENV


def run_mandoc_tree(gz_path: str) -> tuple[str, str]:
    """Run mandoc -T tree on a .gz manpage file, return (stdout, stderr)."""
    env = _get_mandoc_env()
    result = subprocess.run(
        ["mandoc", "-T", "tree", gz_path],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(f"mandoc -T tree failed for {gz_path}: {result.stderr}")
    # mandoc emits \x1e (record separator) as soft-hyphen markers in the tree
    # output.  Python's splitlines() treats \x1e as a line break, which would
    # corrupt the tree structure.  Replace with '-' to preserve hyphenated words.
    return result.stdout.replace("\x1e", "-"), result.stderr


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------

# Section names that contain options (mirrors roff_parser._OPTION_SECTION_NAMES)
_OPTION_SECTION_NAMES = {
    "options",
    "other options",
    "common options",
    "description",
    "function letters",
    "command options",
    "optional arguments",
    "positional arguments",
    "positional options",
    "global options",
    "arguments",
    "flags",
    "tests",
    "actions",
}


def _is_option_section(name: str) -> bool:
    """Check if a section name is one that typically contains options."""
    n = name.lower().strip()
    if n in _OPTION_SECTION_NAMES:
        return True
    if "option" in n:
        return True
    return False


def _get_section_name(section_node: Node) -> str:
    """Extract the section name from a SH/Sh/SS block node."""
    head = section_node.get_head()
    if head:
        return head.get_text().strip()
    return ""


def _get_sections(root: Node) -> list[Node]:
    """Get all top-level section block nodes (SH or Sh)."""
    result = []
    for child in root.children:
        if child.kind in ("SH", "Sh") and child.subkind == "block":
            result.append(child)
    return result


# ---------------------------------------------------------------------------
# Text extraction from tree nodes
# ---------------------------------------------------------------------------


def _collect_text_from_body(body: Node) -> str:
    """Collect description text from a body node, preserving paragraph breaks.

    Handles text nodes, inline formatting elements (B, I, BR, etc.),
    and paragraph break markers (PP, sp).
    """
    parts = []
    _collect_body_parts(body, parts)
    # Join and clean up
    text = " ".join(parts)
    # Normalize paragraph breaks
    text = re.sub(r"\s*\n\n\s*", "\n\n", text)
    text = re.sub(r"  +", " ", text)
    return clean_roff(text).strip()


def _collect_body_parts(node: Node, parts: list):
    """Recursively collect text parts from a body node."""
    for child in node.children:
        if child.subkind == "text":
            if child.text:
                parts.append(child.text)
            # Text nodes may have child text nodes (e.g. content inside
            # nf/fi no-fill blocks in mandoc tree output).
            if child.children:
                _collect_body_parts(child, parts)
        elif child.kind in ("PP", "LP", "P") and child.subkind == "block":
            # Paragraph break
            parts.append("\n\n")
            body = child.get_body()
            if body:
                _collect_body_parts(body, parts)
        elif child.kind == "sp" and child.subkind == "elem":
            parts.append("\n\n")
        elif (
            child.kind in ("B", "I", "BI", "BR", "IB", "IR", "RB", "RI", "SM", "SB")
            and child.subkind == "elem"
        ):
            # Inline formatting — collect text from children
            parts.append(child.get_text())
        elif child.kind == "RS" and child.subkind == "block":
            # Indented block — include its content
            body = child.get_body()
            if body:
                _collect_body_parts(body, parts)
        elif child.kind in ("nf", "fi", "PD"):
            # No-fill / fill mode / paragraph-distance — skip
            continue
        elif child.kind == "Dl" and child.subkind == "block":
            # Display line (example) — include content
            parts.append("\n\n")
            body = child.get_body()
            if body:
                _collect_body_parts(body, parts)
            parts.append("\n\n")
        elif child.kind == "Fl" and child.subkind == "elem":
            # mdoc flag reference — prepend dash
            parts.append("-" + child.get_text())
        elif child.kind == "Xr" and child.subkind == "elem":
            # mdoc cross-reference — format as name(section)
            texts = []
            child._collect_text(texts)
            if len(texts) >= 2:
                parts.append(f"{texts[0]}({texts[1]})")
            else:
                parts.append(child.get_text())
        elif child.subkind == "block":
            # Other blocks — recurse into body
            body = child.get_body()
            if body:
                _collect_body_parts(body, parts)
        elif child.subkind == "elem":
            # Other elements — collect text
            parts.append(child.get_text())
        # Skip head nodes of sub-blocks (they're structural, not content)


# Matches TP/IP indent width values: pure digits or digit+unit suffix
# e.g. "14", "0.5i", "72u", "3n", "1.5c"
_TP_INDENT_RE = re.compile(r"^[\d.]+[icnpuPm]?(?:\+\d+)?$")


def _collect_head_text(head: Node) -> str:
    """Collect the flag text from a head node.

    Skips leading and trailing bare text nodes that are TP/IP indent width
    parameters (e.g. '14', '0.5i', '72u', '4'), not flag text.
    """
    # Gather (text, is_indent_width) tuples, then strip leading/trailing indent widths
    items: list[tuple[str, bool]] = []
    for child in head.children:
        if child.subkind == "text":
            is_indent = _TP_INDENT_RE.match(child.text.strip()) is not None
            items.append((child.text, is_indent))
        elif child.subkind == "elem":
            items.append((child.get_text(), False))

    # Strip leading indent-width items
    while items and items[0][1]:
        items.pop(0)
    # Strip trailing indent-width items
    while items and items[-1][1]:
        items.pop()

    text = _smart_join([t for t, _ in items]).strip()
    # Replace roff non-breaking space (\ ) with regular space so _parse_flag_text
    # can properly separate flags from arguments (e.g. '-C\ file' → '-C file').
    text = text.replace("\\ ", " ")
    # Remove roff zero-width/thin-space escapes (\^, \|, \&) that mandoc preserves
    # in the tree output (e.g. '\-\^\-context' → '\-\-context').
    text = re.sub(r"\\[|^&]", "", text)
    return text


_FLAG_START_RE = re.compile(r"^-[A-Za-z]")


def _rs_has_flags(rs_body: Node) -> bool:
    """Check if an RS body contains flag-like TP/IP entries.

    A flag must start with a dash followed by a letter (e.g. -v, --help),
    not just a dash followed by digits or punctuation.
    """
    for child in rs_body.children:
        if child.kind in ("TP", "IP") and child.subkind == "block":
            head = child.get_head()
            if head:
                text = clean_roff(_collect_head_text(head)).strip()
                if _FLAG_START_RE.match(text):
                    return True
    return False


# ---------------------------------------------------------------------------
# Option extraction: man(7) pages — TP, IP, HP, PP+RS patterns
# ---------------------------------------------------------------------------


def _extract_man_options(body: Node) -> tuple[list[dict], int, int]:
    """Extract options from a man(7) section body node.

    Returns (entries, total_children, unrecognized_count).

    Handles:
    - TP (block): tag in head, description in body
    - IP (block): tag in head, description in body
    - HP (block): first line of body is the flag
    - PP (block) followed by RS (block): DocBook/git style
    - PP-only (block): go-md2man/cobra style, flag in first text child
    - Bare text nodes starting with bold flag formatting
    """
    entries = []
    children = body.children
    total_children = len(children)
    unrecognized = 0

    i = 0
    while i < len(children):
        child = children[i]

        if child.kind in ("TP", "IP") and child.subkind == "block":
            entry = _extract_tp_ip_entry(child)
            if entry:
                # Consume continuation siblings: RS blocks and empty-headed
                # IP/TP blocks that extend this option's description.
                while i + 1 < len(children):
                    nxt = children[i + 1]
                    if nxt.kind == "RS" and nxt.subkind == "block":
                        rs_body = nxt.get_body()
                        if rs_body and not _rs_has_flags(rs_body):
                            extra = _collect_text_from_body(rs_body)
                            if extra:
                                entry["description"] = (
                                    entry.get("description", "") + "\n\n" + extra
                                ).strip()
                            i += 1
                            continue
                    if nxt.kind in ("TP", "IP") and nxt.subkind == "block":
                        nxt_head = nxt.get_head()
                        head_text = _collect_head_text(nxt_head) if nxt_head else ""
                        if not head_text.strip():
                            nxt_body = nxt.get_body()
                            if nxt_body:
                                extra = _collect_text_from_body(nxt_body)
                                if extra:
                                    entry["description"] = (
                                        entry.get("description", "") + "\n\n" + extra
                                    ).strip()
                            i += 1
                            continue
                    break
                # Consume TQ continuation blocks (strace pattern: TP short flag + TQ long flag with description)
                while i + 1 < len(children):
                    nxt = children[i + 1]
                    if nxt.kind == "TQ" and nxt.subkind == "block":
                        tq_head = nxt.get_head()
                        tq_body = nxt.get_body()
                        if tq_head:
                            tq_flag = clean_roff(_collect_head_text(tq_head)).strip()
                            tq_parsed = _parse_flag_text(tq_flag)
                            # Merge long flags from TQ into entry
                            for f in tq_parsed.get("long", []):
                                if f not in entry.get("long", []):
                                    entry.setdefault("long", []).append(f)
                            # Update flag_text
                            if tq_flag:
                                entry["flag_text"] = (
                                    entry.get("flag_text", "") + ", " + tq_flag
                                )
                        if tq_body and not entry.get("description", "").strip():
                            entry["description"] = _collect_text_from_body(tq_body)
                        i += 1
                        continue
                    break
                entries.append(entry)
            i += 1

        elif child.kind == "HP" and child.subkind == "block":
            entry = _extract_hp_entry(child)
            if entry:
                # Check if next sibling is IP with empty head (HP+IP pattern: sed style)
                if i + 1 < len(children):
                    nxt = children[i + 1]
                    if nxt.kind == "IP" and nxt.subkind == "block":
                        nxt_head = nxt.get_head()
                        head_text = _collect_head_text(nxt_head) if nxt_head else ""
                        if not head_text.strip():
                            nxt_body = nxt.get_body()
                            if nxt_body:
                                extra = _collect_text_from_body(nxt_body)
                                if extra:
                                    entry["description"] = extra
                            i += 1  # consume the IP
                entries.append(entry)
            i += 1

        elif child.kind == "PP" and child.subkind == "block":
            # Check if this is a PP+RS pattern (DocBook/git style):
            # PP body has flag text, next sibling is RS with description
            entry, consumed = _extract_pp_rs_entry(children, i)
            if entry:
                entries.append(entry)
                i += consumed
            else:
                # Try PP-only pattern (go-md2man/cobra): flag in first
                # text child of PP body, description in subsequent text children
                pp_body = child.get_body()
                entry = _extract_pp_only_entry(pp_body) if pp_body else None
                if entry:
                    entries.append(entry)
                else:
                    # Fall back: recurse into PP body for nested TP/IP
                    if pp_body:
                        sub_entries, _, _ = _extract_man_options(pp_body)
                        entries.extend(sub_entries)
                i += 1

        elif child.kind == "RS" and child.subkind == "block":
            # Check if previous sibling was a bare text node with flag text
            # (git --version pattern: bare text followed by RS)
            if i > 0 and children[i - 1].subkind == "text":
                prev_text = clean_roff(children[i - 1].text).strip()
                if prev_text.startswith("-"):
                    rs_body = child.get_body()
                    description = ""
                    if rs_body:
                        description = _collect_text_from_body(rs_body)
                    parsed = _parse_flag_text(prev_text)
                    parsed["flag_text"] = prev_text
                    parsed["description"] = description
                    if parsed.get("short") or parsed.get("long"):
                        entries.append(parsed)
                        i += 1
                        continue

            # Standalone RS block — recurse into its body
            rs_body = child.get_body()
            if rs_body:
                sub_entries, _, _ = _extract_man_options(rs_body)
                entries.extend(sub_entries)
            i += 1

        elif child.kind in ("SS", "Ss") and child.subkind == "block":
            # Check if SS head itself is a flag (netstat pattern)
            ss_head = child.get_head()
            if ss_head:
                head_text = clean_roff(_collect_head_text(ss_head)).strip()
                if head_text.startswith("-"):
                    parsed = _parse_flag_text(head_text)
                    parsed["flag_text"] = head_text
                    ss_body = child.get_body()
                    parsed["description"] = (
                        _collect_text_from_body(ss_body) if ss_body else ""
                    )
                    if parsed.get("short") or parsed.get("long"):
                        entries.append(parsed)
                        i += 1
                        continue
            # Fall back: recurse into body for nested TP/IP
            ss_body = child.get_body()
            if ss_body:
                sub_entries, _, _ = _extract_man_options(ss_body)
                entries.extend(sub_entries)
            i += 1

        elif child.subkind == "text":
            # Bare text node with bold flag formatting (go-md2man first option)
            entry = _extract_bare_text_entry(child)
            if entry:
                entries.append(entry)
            i += 1

        else:
            unrecognized += 1
            i += 1

    return _merge_short_long_pairs(entries), total_children, unrecognized


def _merge_short_long_pairs(entries: list[dict]) -> list[dict]:
    """Merge consecutive short-only + long-only TP/IP entries into one.

    Pattern: a short-flag entry with little/no description is followed by a
    long-flag entry with the actual description.  The two describe the same
    option (e.g. -B / --buffer-size in tcpdump).
    """
    merged = []
    i = 0
    while i < len(entries):
        e = entries[i]
        if (
            i + 1 < len(entries)
            and e.get("short")
            and not e.get("long")
            and not e.get("description", "").strip()
        ):
            nxt = entries[i + 1]
            if nxt.get("long") and not nxt.get("short"):
                # Merge: take short from first, long+desc from second
                m = dict(nxt)
                m["short"] = e["short"]
                if e.get("positional") and not m.get("positional"):
                    m["positional"] = e["positional"]
                    m["has_argument"] = True
                # Rebuild flag_text
                flag = ", ".join(e["short"] + m["long"])
                if m.get("positional"):
                    flag += " " + m["positional"]
                m["flag_text"] = flag
                merged.append(m)
                i += 2
                continue
        merged.append(e)
        i += 1
    return merged


def _extract_tp_ip_entry(node: Node) -> dict | None:
    """Extract an option entry from a TP or IP block node."""
    head = node.get_head()
    body = node.get_body()

    if not head:
        return None

    flag_text = _collect_head_text(head)
    if not flag_text:
        return None

    description = ""
    if body:
        description = _collect_text_from_body(body)

    parsed = _parse_flag_text(flag_text)
    parsed["flag_text"] = clean_roff(flag_text)
    parsed["description"] = description
    return parsed


def _extract_hp_entry(node: Node) -> dict | None:
    """Extract an option entry from an HP block node.

    HP puts the flag on the first text line of the body.
    """
    body = node.get_body()
    if not body or not body.children:
        return None

    # First text-bearing child is the flag
    flag_text = ""
    desc_start = 0
    for j, child in enumerate(body.children):
        if child.subkind == "text" or child.subkind == "elem":
            flag_text = child.get_text() if child.subkind == "elem" else child.text
            desc_start = j + 1
            break

    if not flag_text:
        return None

    # Remaining children are description
    desc_parts = []
    for child in body.children[desc_start:]:
        if child.subkind == "text":
            desc_parts.append(child.text)
        elif child.subkind == "elem":
            desc_parts.append(child.get_text())

    parsed = _parse_flag_text(flag_text)
    parsed["flag_text"] = clean_roff(flag_text)
    parsed["description"] = clean_roff(" ".join(desc_parts))
    return parsed


def _extract_pp_rs_entry(children: list[Node], idx: int) -> tuple[dict | None, int]:
    """Try to extract a PP+RS entry (DocBook/git style).

    Pattern: PP block with flag text in body, followed by RS block with description.
    Returns (entry_dict_or_None, number_of_children_consumed).
    """
    pp = children[idx]
    pp_body = pp.get_body()
    if not pp_body:
        return None, 1

    # Get the flag text from PP body
    flag_text = _collect_head_text(pp_body)
    if not flag_text:
        return None, 1

    # Check if it looks like a flag
    cleaned_flag = clean_roff(flag_text).strip()
    if not cleaned_flag or not (
        cleaned_flag.startswith("-") or cleaned_flag.startswith("<")
    ):
        return None, 1

    # Look for RS block as next sibling
    if idx + 1 >= len(children):
        return None, 1
    next_node = children[idx + 1]
    if next_node.kind != "RS" or next_node.subkind != "block":
        return None, 1

    # Extract description from RS body
    rs_body = next_node.get_body()
    description = ""
    if rs_body:
        description = _collect_text_from_body(rs_body)

    parsed = _parse_flag_text(cleaned_flag)
    parsed["flag_text"] = cleaned_flag
    parsed["description"] = description
    return parsed, 2


# Bold flag pattern: \fB-...\fP or \fB-...\fR or \fB--...
# Bold flag: \fB-..., \fB\-..., or \fB\\-...
_BOLD_FLAG_RE = re.compile(r"\\fB\s*\\?\\?-")


def _extract_pp_only_entry(pp_body: Node) -> dict | None:
    """Try to extract a PP-only entry (go-md2man/cobra style).

    Pattern: PP body's first text child contains bold flag formatting
    (\\fB--flag\\fP), description in subsequent text children.
    """
    if not pp_body.children:
        return None

    # Find first text child
    first_text = None
    for child in pp_body.children:
        if child.subkind == "text":
            first_text = child
            break

    if not first_text or not _BOLD_FLAG_RE.match(first_text.text.strip()):
        return None

    flag_text = clean_roff(first_text.text).strip()
    if not flag_text.startswith("-"):
        return None

    # Description is in child text nodes (mandoc indents them under the flag)
    # plus any subsequent text/elem siblings after the flag in the PP body
    desc_parts = []
    for child in first_text.children:
        if child.subkind == "text":
            desc_parts.append(child.text)
    # Also collect text siblings after the first text node
    found_first = False
    for child in pp_body.children:
        if child is first_text:
            found_first = True
            continue
        if not found_first:
            continue
        if child.kind == "sp" and child.subkind == "elem":
            break  # sp marks end of this option entry
        if child.subkind == "text":
            desc_parts.append(child.text)
        elif child.subkind == "elem":
            desc_parts.append(child.get_text())

    description = clean_roff(" ".join(desc_parts)).strip()

    parsed = _parse_flag_text(flag_text)
    parsed["flag_text"] = flag_text
    parsed["description"] = description
    return parsed


def _extract_bare_text_entry(node: Node) -> dict | None:
    """Try to extract an option from a bare text node with bold flag formatting.

    Pattern: text node at body level starting with \\fB-flag, with description
    text as children (due to mandoc indentation).
    """
    text = node.text.strip() if node.text else ""
    if not _BOLD_FLAG_RE.match(text):
        return None

    flag_text = clean_roff(text).strip()
    if not flag_text.startswith("-"):
        return None

    # Description is in child text nodes (indented under this text node)
    desc_parts = []
    for child in node.children:
        if child.subkind == "text":
            desc_parts.append(child.text)

    description = clean_roff(" ".join(desc_parts)).strip()

    parsed = _parse_flag_text(flag_text)
    parsed["flag_text"] = flag_text
    parsed["description"] = description
    return parsed


# ---------------------------------------------------------------------------
# Option extraction: mdoc pages — Bl/It patterns
# ---------------------------------------------------------------------------


def _extract_mdoc_options(body: Node) -> tuple[list[dict], int, int]:
    """Extract options from an mdoc section body node.

    Returns (entries, total_items, skipped_count).

    mdoc options live in .Bl -tag lists as .It entries.
    """
    entries = []
    total_items = 0
    skipped = 0

    for bl in body.find_recursive("Bl", "block"):
        bl_body = bl.get_body()
        if not bl_body:
            continue

        for it in bl_body.find("It", "block"):
            total_items += 1
            entry = _extract_it_entry(it)
            if entry:
                entries.append(entry)
            else:
                skipped += 1

    return entries, total_items, skipped


def _extract_it_entry(node: Node) -> dict | None:
    """Extract an option entry from an mdoc It (list item) block."""
    head = node.get_head()
    body = node.get_body()

    if not head:
        return None

    # Build flag info from semantic mdoc elements in the head
    short = []
    long = []
    has_argument = False
    argument = None
    flag_parts = []

    for child in head.children:
        if child.kind == "Fl" and child.subkind == "elem":
            # Flag element — mandoc may escape dashes as \-
            flag_val = child.get_text().strip().lstrip("\\")
            if flag_val:
                flag_parts.append(f"-{flag_val}")
                if flag_val.startswith("-"):
                    # Double dash: --foo
                    long.append(f"-{flag_val}")
                else:
                    short.append(f"-{flag_val}")
        elif child.kind == "Ar" and child.subkind == "elem":
            # Argument element
            arg_val = child.get_text().strip()
            if arg_val:
                has_argument = True
                if not argument:
                    argument = arg_val
                flag_parts.append(arg_val)
        elif child.kind == "Cm" and child.subkind == "elem":
            # Command modifier (e.g. @ in bsdtar)
            cm_val = child.get_text().strip()
            if cm_val:
                flag_parts.append(cm_val)
        elif child.kind == "Ns" and child.subkind == "elem":
            # No-space — skip, it's just a formatting hint
            continue
        elif child.kind == "Op" and child.subkind == "block":
            # Optional element — recurse
            op_body = child.get_body()
            if op_body:
                for oc in op_body.children:
                    if oc.kind == "Fl" and oc.subkind == "elem":
                        fv = oc.get_text().strip()
                        if fv:
                            flag_parts.append(f"[-{fv}]")
                    elif oc.kind == "Ar" and oc.subkind == "elem":
                        av = oc.get_text().strip()
                        if av:
                            flag_parts.append(f"[{av}]")
        elif child.subkind == "text":
            # Separator text like ",", "|"
            t = child.text.strip().rstrip(".")
            if t and t not in (",", "|"):
                flag_parts.append(t)

    if not short and not long and not argument:
        # Check if head has raw text that looks like a flag (fallback)
        raw = _collect_head_text(head)
        if raw:
            parsed = _parse_flag_text(raw)
            if parsed.get("short") or parsed.get("long"):
                short = parsed.get("short", [])
                long = parsed.get("long", [])
                has_argument = parsed.get("has_argument", False)
                argument = parsed.get("positional")
                flag_parts = [clean_roff(raw)]

    if not short and not long and not flag_parts:
        return None

    flag_text = " ".join(flag_parts) if flag_parts else ", ".join(short + long)

    description = ""
    if body:
        description = _collect_text_from_body(body)

    return {
        "short": short,
        "long": long,
        "has_argument": has_argument,
        "positional": argument,
        "flag_text": clean_roff(flag_text),
        "description": description,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_options(gz_path: str) -> ExtractionResult:
    """Extract options from a .gz man page using mandoc -T tree.

    Returns an ExtractionResult with options and diagnostic metadata.
    """
    result = ExtractionResult()

    try:
        tree_text, stderr = run_mandoc_tree(gz_path)
    except Exception as e:
        logger.warning("mandoc -T tree failed for %s: %s", gz_path, e)
        return result

    result.tree_text = tree_text
    result.mandoc_stderr = stderr

    root = parse_tree(tree_text)
    if not root.children:
        return result

    sections = _get_sections(root)

    # Detect dialect from node names: Sh = mdoc, SH = man
    is_mdoc = any(s.kind == "Sh" for s in sections)
    result.is_mdoc = is_mdoc

    # Pass 1: extract from option-named sections
    raw = []
    total_body_children = 0
    total_unrecognized = 0

    for section in sections:
        name = _get_section_name(section)
        if not _is_option_section(name):
            continue
        result.option_sections_found += 1
        body = section.get_body()
        if not body:
            result.option_sections_empty += 1
            continue
        if is_mdoc:
            entries, items, skipped = _extract_mdoc_options(body)
            total_body_children += items
            total_unrecognized += skipped
        else:
            entries, children, unrec = _extract_man_options(body)
            total_body_children += children
            total_unrecognized += unrec
        if not entries:
            result.option_sections_empty += 1
        raw.extend(entries)

    result.total_body_children = total_body_children
    result.unrecognized_children = total_unrecognized

    # Pass 2: scan all sections for entries with actual flags (supplement)
    existing_flags = set()
    for e in raw:
        for f in e.get("short", []) + e.get("long", []):
            existing_flags.add(f)

    for section in sections:
        name = _get_section_name(section)
        if _is_option_section(name):
            continue  # Already scanned
        body = section.get_body()
        if not body:
            continue
        if is_mdoc:
            extra, _, _ = _extract_mdoc_options(body)
        else:
            extra, _, _ = _extract_man_options(body)
        for e in extra:
            flags = e.get("short", []) + e.get("long", [])
            overlap = [f for f in flags if f in existing_flags]
            if flags and not overlap:
                raw.append(e)
                existing_flags.update(flags)
            elif overlap and e.get("description", "").strip():
                # Replace existing entry if new one has longer description
                new_desc = e.get("description", "")
                for j, existing in enumerate(raw):
                    ex_flags = existing.get("short", []) + existing.get("long", [])
                    if any(f in ex_flags for f in overlap):
                        if len(new_desc) > len(existing.get("description", "")):
                            raw[j] = e
                            existing_flags.update(flags)
                        break

    # Convert to models.Option objects
    options = []
    empty_desc = 0
    for entry in raw:
        short = entry.get("short", [])
        long = entry.get("long", [])
        has_argument = entry.get("has_argument", False)
        argument = entry.get("positional") or None
        description = entry.get("description", "")

        if not short and not long and not argument:
            continue

        if not description.strip():
            empty_desc += 1

        flag_text = entry.get("flag_text", "")
        if not flag_text:
            flag_text = ", ".join(short + long)
            if argument:
                flag_text += " " + argument

        if flag_text and description:
            text = flag_text + "\n" + description
        elif flag_text:
            text = flag_text
        else:
            text = description

        options.append(
            models.Option(
                text=text,
                short=short,
                long=long,
                has_argument=has_argument,
                positional=argument,
                nested_cmd=False,
            )
        )

    result.options = options
    result.empty_description_count = empty_desc
    return result


def assess_confidence(result: ExtractionResult) -> ConfidenceResult:
    """Assess whether the tree parser produced a trustworthy extraction.

    Returns a ConfidenceResult with confident=True/False and reasons.
    """
    reasons = []

    # All option sections empty
    if (
        result.option_sections_found > 0
        and result.option_sections_empty == result.option_sections_found
    ):
        reasons.append("all option sections empty")

    # No option sections found and no options extracted
    if not result.options and result.option_sections_found == 0:
        reasons.append("no option sections found")

    # High unrecognized ratio (>50% and >5 absolute)
    if (
        result.total_body_children > 0
        and result.unrecognized_children > 5
        and result.unrecognized_children / result.total_body_children > 0.5
    ):
        reasons.append(
            f"high unrecognized ratio: {result.unrecognized_children}"
            f"/{result.total_body_children}"
        )

    # High empty descriptions (>50% and >3 absolute)
    if (
        len(result.options) > 0
        and result.empty_description_count > 3
        and result.empty_description_count / len(result.options) > 0.5
    ):
        reasons.append(
            f"high empty descriptions: {result.empty_description_count}"
            f"/{len(result.options)}"
        )

    # mandoc errors
    if result.mandoc_stderr:
        stderr_upper = result.mandoc_stderr.upper()
        for keyword in ("UNSUPP", "ERROR", "FATAL"):
            if keyword in stderr_upper:
                reasons.append(f"mandoc stderr contains {keyword}")
                break

    return ConfidenceResult(confident=len(reasons) == 0, reasons=reasons)
