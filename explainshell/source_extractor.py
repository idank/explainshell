"""
Source-based man page option extractor.

Extracts options directly from the roff source of man pages,
without using an LLM.

Public API:
    extract(gz_path) -> store.ParsedManpage
    detect_dashless_opts(gz_path) -> bool
"""

import gzip
import logging
import os
import re

from explainshell import errors, manpage, roff_parser, store

logger = logging.getLogger(__name__)

# Pattern for bare option letters: {A|c|d|...}
_BARE_LETTERS_BRACES = re.compile(
    r"\{([A-Za-z0-9](?:\|[A-Za-z0-9])*)\}"
)


def _is_section_header(line, name):
    """Check if a roff line is a section header (.SH or .Sh) matching name."""
    stripped = line.strip()
    # man(7) format: .SH NAME / .SH "NAME"
    # mdoc(7) format: .Sh NAME
    for macro in (".SH", ".Sh"):
        if stripped.startswith(macro):
            rest = stripped[len(macro):].strip().strip('"').strip()
            if rest.upper() == name.upper():
                return True
    return False


def _is_any_section_header(line):
    """Check if a roff line is any section header (.SH or .Sh)."""
    stripped = line.strip()
    return stripped.startswith(".SH ") or stripped.startswith(".Sh ")


def _is_subsection_header(line):
    """Check if a roff line is a subsection header (.SS)."""
    return line.strip().startswith(".SS ")


def _extract_section(lines, section_name):
    """Extract lines between a section header and the next section header."""
    result = []
    in_section = False
    for line in lines:
        if in_section:
            if _is_any_section_header(line):
                break
            result.append(line)
        elif _is_section_header(line, section_name):
            in_section = True
    return result


def detect_dashless_opts(gz_path: str) -> bool:
    """Detect whether a man page supports dashless (old-style/BSD) options.

    Checks the SYNOPSIS and DESCRIPTION sections for indicators that
    options can be specified without a leading dash.
    """
    try:
        with gzip.open(gz_path, "rt", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning("Failed to read %s for dashless detection: %s", gz_path, e)
        return False

    # --- A. Check SYNOPSIS section ---
    synopsis_lines = _extract_section(lines, "SYNOPSIS")
    for line in synopsis_lines:
        # A1: Subsection keyword containing "traditional" or "old"
        if _is_subsection_header(line):
            header_text = line.strip()[4:].strip().strip('"').lower()
            if "traditional" in header_text or "old" in header_text:
                logger.info("dashless_opts: found traditional/old subsection in SYNOPSIS")
                return True

        # A2: Bare option letters like {A|c|d|...}
        cleaned = roff_parser.clean_roff(line)
        if _BARE_LETTERS_BRACES.search(cleaned):
            logger.info("dashless_opts: found bare letters in braces in SYNOPSIS")
            return True

    # --- B. Check DESCRIPTION section ---
    desc_lines = _extract_section(lines, "DESCRIPTION")
    desc_text = " ".join(roff_parser.clean_roff(line) for line in desc_lines).lower()

    # B1: "BSD" near "option" and "without"/"must not" near "dash"
    if "bsd" in desc_text and "option" in desc_text:
        if ("without" in desc_text or "must not" in desc_text) and "dash" in desc_text:
            logger.info("dashless_opts: found BSD-style dashless option mention in DESCRIPTION")
            return True

    return False


def extract(gz_path: str) -> store.ParsedManpage:
    """Extract options from raw roff source.

    Raises errors.ExtractionError if the roff parser finds no options.
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)

    options = roff_parser.parse_options(gz_path)
    if not options:
        raise errors.ExtractionError(
            f"roff parser found no options in {os.path.basename(gz_path)}"
        )

    logger.info(
        "roff parser extracted %d options from %s", len(options), gz_path
    )
    return store.ParsedManpage(
        source=os.path.basename(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=detect_dashless_opts(gz_path),
    )
