"""Roff source detection utilities.

Reads raw roff source to detect manpage properties that the LLM
extractor doesn't supply directly.

Public API:
    detect_nested_cmd(gz_path) -> bool
"""

import gzip
import logging
import re

logger = logging.getLogger(__name__)


def _clean_roff(text: str) -> str:
    """Strip roff escape sequences and formatting from text."""
    # Remove font escapes: \fB, \fI, \fR, \fP, \f(xx
    text = re.sub(r"\\f[BIRP]", "", text)
    text = re.sub(r"\\f\(..", "", text)
    # \- → -
    text = text.replace("\\-", "-")
    # \(en, \(em → -
    text = re.sub(r"\\\(en|\\\(em", "-", text)
    # \& (zero-width space) → empty
    text = text.replace("\\&", "")
    # \e → backslash
    text = text.replace("\\e", "\\")
    # \(aq, \(cq → '
    text = text.replace("\\(aq", "'")
    text = text.replace("\\(cq", "'")
    # \(lq, \(rq → "
    text = re.sub(r"\\\(lq|\\\(rq", '"', text)
    # \(bu → bullet (just remove)
    text = text.replace("\\(bu", "")
    # \~, \0, \<space> → space
    text = text.replace("\\~", " ")
    text = text.replace("\\0", " ")
    text = text.replace("\\ ", " ")
    # Remove \m[...] color directives
    text = re.sub(r"\\m\[[^\]]*\]", "", text)
    # Remove \s-N and \s+N size changes
    text = re.sub(r"\\s[-+]?\d+", "", text)
    # Remove \u (superscript), \d (subscript), \c, \:, \^, \|
    for esc in ("\\u", "\\d", "\\c", "\\:", "\\^", "\\|"):
        text = text.replace(esc, "")
    # Remove \n(.x register references
    text = re.sub(r"\\n\(?\w+", "", text)
    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text)
    return text.strip()


def _is_section_header(line: str, name: str) -> bool:
    """Check if a roff line is a section header (.SH or .Sh) matching name."""
    stripped = line.strip()
    # man(7): .SH NAME / .SH "NAME"; mdoc(7): .Sh NAME
    for macro in (".SH", ".Sh"):
        if stripped.startswith(macro):
            rest = stripped[len(macro) :].strip().strip('"').strip()
            if rest.upper() == name.upper():
                return True
    return False


def _is_any_section_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(".SH ") or stripped.startswith(".Sh ")


def _extract_section(lines: list[str], section_name: str) -> list[str]:
    """Extract lines between a section header and the next section header."""
    result: list[str] = []
    in_section = False
    for line in lines:
        if in_section:
            if _is_any_section_header(line):
                break
            result.append(line)
        elif _is_section_header(line, section_name):
            in_section = True
    return result


# Matches "command" as a standalone word, case-insensitive
_COMMAND_WORD = re.compile(r"\bcommand\b", re.IGNORECASE)
# Matches "command" inside angle brackets: <command>
_COMMAND_ANGLE = re.compile(r"<command>", re.IGNORECASE)
# Matches "command" as part of an option name: --foo-command, --command-foo
_COMMAND_IN_OPT = re.compile(r"-\w*command\w*", re.IGNORECASE)


def detect_nested_cmd(gz_path: str) -> bool:
    """Detect whether a man page's positional args start a nested command.

    Checks the SYNOPSIS section for a positional argument named 'command'.
    Excludes angle-bracket patterns like <command> (git subcommand style).
    """
    try:
        with gzip.open(gz_path, "rt", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning("Failed to read %s for nested_cmd detection: %s", gz_path, e)
        return False

    synopsis_lines = _extract_section(lines, "SYNOPSIS")
    for line in synopsis_lines:
        cleaned = _clean_roff(line)
        if not _COMMAND_WORD.search(cleaned):
            continue
        # Exclude <command> (git-style subcommand pattern)
        if _COMMAND_ANGLE.search(cleaned):
            continue
        # Strip option-name occurrences (e.g. --rsh-command) and recheck
        stripped = _COMMAND_IN_OPT.sub("", cleaned)
        if _COMMAND_WORD.search(stripped):
            logger.debug("nested_cmd: found 'command' in SYNOPSIS of %s", gz_path)
            return True

    return False
