"""
Direct roff parser for extracting structured options from man page source.

Reads raw .1.gz files, detects the roff dialect (man vs mdoc), and extracts
option records directly from roff macros — avoiding the lossy mandoc HTML
round-trip.

Public API:
    parse_options(gz_path) -> list[store.Option]
"""

import gzip
import logging
import re

from explainshell import store

logger = logging.getLogger(__name__)

# Sections that contain options (case-insensitive matching on unquoted name)
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

# Macros that start a new option entry in man(7) pages
_TP_LIKE = {".TP", ".IP", ".HP"}

# Macros that signal "end of current option block"
_BLOCK_ENDERS_MAN = {".SH", ".SS", ".PP", ".LP", ".P", ".TP", ".IP", ".HP"}


def parse_options(gz_path: str) -> list:
    """Main entry point: extract options from a .gz man page file.

    Returns a list of store.Option objects, or an empty list if nothing
    could be extracted.
    """
    try:
        lines = _read_roff(gz_path)
    except Exception as e:
        logger.warning("Failed to read %s: %s", gz_path, e)
        return []

    if not lines:
        return []

    dialect = _detect_dialect(lines)

    if dialect == "mdoc":
        raw = _parse_mdoc_options(lines)
    else:
        raw = _parse_man_options(lines)

    # Convert raw dicts to store.Option objects, filtering out non-option entries
    options = []
    idx = 0
    for entry in raw:
        short = entry.get("short", [])
        long = entry.get("long", [])
        expects_arg = entry.get("expects_arg", False)
        argument = entry.get("argument") or None
        description = entry.get("description", "")
        nested_cmd = False

        # Skip entries with no flags and no argument
        if not short and not long and not argument:
            continue

        p = store.Paragraph(idx, description, "OPTIONS", True)
        options.append(store.Option(p, short, long, expects_arg, argument, nested_cmd))
        idx += 1

    return options


def _read_roff(gz_path: str) -> list:
    """Decompress and read lines from a .gz roff file."""
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _detect_dialect(lines: list) -> str:
    """Detect whether the man page uses man(7) or mdoc(7) macros.

    Returns "mdoc" if .Dt is found (mdoc header), otherwise "man".
    """
    for line in lines[:50]:
        stripped = line.strip()
        if stripped.startswith(".Dt ") or stripped.startswith(".Dd "):
            return "mdoc"
        if stripped.startswith(".TH "):
            return "man"
    return "man"


def _in_option_section(section_name: str) -> bool:
    """Check if the given section name is one that contains options."""
    # Strip quotes and normalize
    name = section_name.strip().strip('"').strip("'").lower()
    return name in _OPTION_SECTION_NAMES


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
    # \(aq → '
    text = text.replace("\\(aq", "'")
    # \(cq → '
    text = text.replace("\\(cq", "'")
    # \(lq, \(rq → "
    text = re.sub(r"\\\(lq|\\\(rq", '"', text)
    # \(bu → bullet (just remove)
    text = text.replace("\\(bu", "")
    # \~ → space
    text = text.replace("\\~", " ")
    # \0 → space (digit-width space)
    text = text.replace("\\0", " ")
    # Remove \m[...] color directives
    text = re.sub(r"\\m\[[^\]]*\]", "", text)
    # Remove \s-N and \s+N size changes
    text = re.sub(r"\\s[-+]?\d+", "", text)
    # Remove \u (superscript), \d (subscript)
    text = text.replace("\\u", "")
    text = text.replace("\\d", "")
    # Remove \n(.x register references
    text = re.sub(r"\\n\(?\w+", "", text)
    # .br, .sp, .fi, .nf, .nh, .hy, .ad, .na - standalone formatting directives
    text = re.sub(r"^\.(br|sp|fi|nf|nh|hy|ad|na|PD|PD \d+)\s*$", "", text, flags=re.MULTILINE)
    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text)
    return text.strip()


def _parse_roff_args(args_str: str) -> list:
    """Parse roff macro arguments, handling quoted strings.

    .BI "--file=" FILE → ["--file=", "FILE"]
    .BI "-a " file → ["-a ", "file"]
    """
    parts = []
    i = 0
    while i < len(args_str):
        if args_str[i] == '"':
            # Find closing quote
            end = args_str.find('"', i + 1)
            if end == -1:
                parts.append(args_str[i + 1:])
                break
            parts.append(args_str[i + 1:end])
            i = end + 1
        elif args_str[i] in (' ', '\t'):
            i += 1
        else:
            # Unquoted argument — runs to next space
            end = i
            while end < len(args_str) and args_str[end] not in (' ', '\t', '"'):
                end += 1
            parts.append(args_str[i:end])
            i = end
    return parts


def _parse_flag_text(text: str) -> dict:
    """Parse a flag/tag line into structured option data.

    Handles formats like:
      -n
      -f FILE
      --verbose
      -f, --file FILE
      -f, --file=FILE
      --color=always|never|auto
      \\fB-n\\fR
      \\fB--file\\fR=\\fIFILE\\fR
      -c <name>=<value>
      --exec-path[=<path>]
    """
    # Clean roff formatting first
    cleaned = _clean_roff(text).strip()

    if not cleaned:
        return {}

    short = []
    long = []
    expects_arg = False
    argument = None

    # Handle alternating-font macros (.BI, .BR, .IR, .RB, .RI)
    # These concatenate their arguments with alternating fonts:
    #   .BI "--file=" FILE → "--file=FILE"
    #   .BI "-a " file → "-a file"
    macro_type = None
    m_altfont = re.match(r"^\.(BI|BR|IB|IR|RB|RI)\s+(.+)$", cleaned)
    if m_altfont:
        macro_type = m_altfont.group(1)
        args_str = m_altfont.group(2)
        # Parse quoted and unquoted arguments
        parts = _parse_roff_args(args_str)
        cleaned = "".join(parts)

    # Remove simple .B, .I macro prefixes
    cleaned = re.sub(r"^\.[BI]\s+", "", cleaned)

    # Strip optional bracket notation around = sign: --flag[=ARG] → --flag=ARG
    cleaned = re.sub(r'\[=', '=', cleaned)
    # Handle -e[eof-str] or -e[ eof-str] pattern: flag with optional arg in brackets
    # Transform -flag[arg] or -flag[ arg] to -flag arg
    cleaned = re.sub(r'(-\w)\[\s*(\w[^\]]*)\]', r'\1 \2', cleaned)
    cleaned = re.sub(r'(--[\w-]+)\[\s*(\w[^\]]*)\]', r'\1 \2', cleaned)
    # Remove remaining brackets
    cleaned = re.sub(r'\[([^\]]*)\]', r'\1', cleaned)

    # Split on ", " to separate flag groups (e.g. "-f, --file" or "-s <s>, --strategy=<s>")
    parts = re.split(r",\s+", cleaned)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Split on space first to get the flag token
        tokens = part.split(None, 1)
        flag = tokens[0]

        # Check if flag itself contains = (e.g. "--file=ARG")
        if "=" in flag:
            flag_part, arg_part = flag.split("=", 1)
            if flag_part.startswith("--"):
                long.append(flag_part)
            elif flag_part.startswith("-"):
                short.append(flag_part)
            if arg_part:
                expects_arg = True
                if not argument:
                    argument = arg_part
            continue

        if flag.startswith("--"):
            long.append(flag)
        elif flag.startswith("-") and len(flag) >= 2:
            short.append(flag)
        else:
            # Might be a positional argument — only if it looks like a
            # well-known placeholder (angle-bracketed like <file>)
            if not short and not long:
                if flag.startswith("<"):
                    argument = flag
                continue

        # Check remaining tokens for argument
        if len(tokens) > 1:
            arg_text = tokens[1].strip().strip("[]")
            if arg_text and not arg_text.startswith("-"):
                expects_arg = True
                if not argument:
                    argument = arg_text

    # Clean up: strip stray brackets from argument
    if argument:
        argument = argument.strip("[]")
        if not argument:
            argument = None

    return {
        "short": short,
        "long": long,
        "expects_arg": expects_arg,
        "argument": argument,
    }


# ---------------------------------------------------------------------------
# man(7) parser — .TP, .IP, .HP, .PP+.RS/.RE patterns
# ---------------------------------------------------------------------------

def _find_option_sections_man(lines: list) -> list:
    """Find line ranges for option-related sections in man(7) pages.

    Returns list of (start_line, end_line) tuples.
    Includes both .SH sections and .SS subsections whose names match
    option-related patterns.
    """
    sections = []
    current_start = None
    in_option_section = False

    # Also track .SS subsections that contain options
    ss_start = None
    in_ss_option = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith(".SH "):
            # Close any open .SS subsection
            if in_ss_option and ss_start is not None:
                sections.append((ss_start, i))
                in_ss_option = False
                ss_start = None

            # Close previous .SH section
            if in_option_section and current_start is not None:
                sections.append((current_start, i))
                in_option_section = False

            section_name = stripped[4:].strip().strip('"')
            if _in_option_section(section_name):
                current_start = i + 1
                in_option_section = True
            else:
                current_start = None

        elif stripped.startswith(".SS ") or stripped.startswith(".SS\t"):
            if in_option_section:
                # .SS within an option .SH — just continue (don't close parent)
                pass
            else:
                # .SS outside an option .SH — check if it's an option subsection
                if in_ss_option and ss_start is not None:
                    sections.append((ss_start, i))
                    in_ss_option = False

                ss_name = stripped[3:].strip().strip('"')
                if _in_option_section(ss_name):
                    ss_start = i + 1
                    in_ss_option = True
                else:
                    ss_start = None

    # Close final sections
    if in_ss_option and ss_start is not None:
        sections.append((ss_start, len(lines)))
    if in_option_section and current_start is not None:
        sections.append((current_start, len(lines)))

    return sections


def _collect_description_lines(lines: list, start: int, end: int) -> list:
    """Collect description text lines from start to end, handling
    continuation and inline macros."""
    desc_lines = []
    i = start
    while i < end:
        line = lines[i].rstrip("\n")
        stripped = line.strip()

        # Stop at next option entry or section
        macro = stripped.split()[0] if stripped.split() else ""
        if macro in (".TP", ".IP", ".HP", ".SH", ".SS"):
            break
        if macro == ".PP" or macro == ".LP" or macro == ".P":
            # In PP+RS mode, .PP starts a new entry at top level
            break

        # Skip pure formatting directives
        if macro in (".PD", ".br", ".sp", ".fi", ".nf", ".nh", ".hy",
                      ".ad", ".na", ".in", ".ti"):
            i += 1
            continue

        # .RS/.RE — just skip the macro line, keep content between
        if macro in (".RS", ".RE"):
            i += 1
            continue

        # .B, .I, .BI, .BR, .IR, .RB, .RI — inline formatted text
        if macro in (".B", ".I", ".BI", ".BR", ".IR", ".RB", ".RI"):
            # Include the text content (without the macro)
            text = stripped[len(macro):].strip()
            if text:
                desc_lines.append(text)
            i += 1
            continue

        # Regular text line
        if stripped and not stripped.startswith(".\\\""):
            desc_lines.append(stripped)

        i += 1

    return desc_lines


def _parse_man_options(lines: list) -> list:
    """Parse options from man(7) format pages using .TP, .IP, .HP, and .PP+.RS/.RE."""
    sections = _find_option_sections_man(lines)
    if not sections:
        return []

    results = []

    for sec_start, sec_end in sections:
        i = sec_start
        while i < sec_end:
            line = lines[i].rstrip("\n")
            stripped = line.strip()

            if not stripped or stripped.startswith(".\\\""):
                i += 1
                continue

            macro = stripped.split()[0] if stripped else ""

            # --- .TP pattern ---
            if macro == ".TP":
                i += 1
                # Skip optional width argument (e.g. ".TP 10")
                # Next non-empty, non-directive line is the tag (flag line)
                while i < sec_end:
                    tag_line = lines[i].rstrip("\n").strip()
                    if tag_line and not tag_line.startswith(".PD"):
                        break
                    i += 1
                else:
                    break

                # Check if this is a .TP followed by another .TP (.PD 0 alias grouping)
                # Collect all flag lines until we hit a non-flag line
                flag_lines = [tag_line]
                i += 1

                # Look ahead for .PD 0 / .TP alias patterns (xargs style)
                while i < sec_end:
                    next_stripped = lines[i].rstrip("\n").strip()
                    if next_stripped == ".PD 0" or next_stripped.startswith(".PD "):
                        i += 1
                        continue
                    if next_stripped == ".PD":
                        i += 1
                        continue
                    if next_stripped == ".TP":
                        i += 1
                        # Next line is another flag alias
                        while i < sec_end:
                            tl = lines[i].rstrip("\n").strip()
                            if tl and not tl.startswith(".PD"):
                                break
                            i += 1
                        else:
                            break
                        flag_lines.append(lines[i].rstrip("\n").strip())
                        i += 1
                        continue
                    break

                # Parse flags from all collected flag lines
                all_short = []
                all_long = []
                has_arg = False
                arg_name = None

                for fl in flag_lines:
                    parsed = _parse_flag_text(fl)
                    if parsed.get("short"):
                        all_short.extend(parsed["short"])
                    if parsed.get("long"):
                        all_long.extend(parsed["long"])
                    if parsed.get("expects_arg"):
                        has_arg = True
                    if parsed.get("argument") and not arg_name:
                        arg_name = parsed["argument"]

                # Skip if no flags found (e.g. escape sequence descriptions)
                if not all_short and not all_long and not arg_name:
                    continue

                # Collect description until next entry
                desc_lines = []
                while i < sec_end:
                    dl = lines[i].rstrip("\n").strip()
                    dm = dl.split()[0] if dl.split() else ""
                    if dm in (".TP", ".IP", ".HP", ".SH", ".SS"):
                        break
                    if dm in (".PP", ".LP", ".P"):
                        # Check if next non-empty line starts a new option
                        # or if this is just a paragraph break in description
                        j = i + 1
                        while j < sec_end:
                            nl = lines[j].rstrip("\n").strip()
                            if nl and not nl.startswith(".PD") and nl != ".PP" and nl != ".LP" and nl != ".P":
                                break
                            j += 1
                        if j < sec_end:
                            nl = lines[j].rstrip("\n").strip()
                            nm = nl.split()[0] if nl.split() else ""
                            # If followed by .RS, this is a new PP+RS entry
                            if nm == ".RS":
                                break
                            # If followed by a flag-like line, it's a new entry
                            if nm in (".TP", ".IP", ".HP"):
                                break
                            cleaned_nl = _clean_roff(nl)
                            if cleaned_nl.startswith("-"):
                                break
                        # Otherwise it's a paragraph break within the description
                        desc_lines.append("")
                        i += 1
                        continue
                    if dm in (".PD", ".br", ".sp", ".fi", ".nf", ".nh",
                              ".hy", ".ad", ".na", ".in", ".ti"):
                        i += 1
                        continue
                    if dm in (".RS", ".RE"):
                        i += 1
                        continue
                    if dm in (".B", ".I", ".BI", ".BR", ".IR", ".RB", ".RI"):
                        text = dl[len(dm):].strip()
                        if text:
                            desc_lines.append(text)
                        i += 1
                        continue
                    if dl and not dl.startswith(".\\\""):
                        desc_lines.append(dl)
                    i += 1

                description = _clean_roff("\n".join(desc_lines))

                results.append({
                    "short": all_short,
                    "long": all_long,
                    "expects_arg": has_arg,
                    "argument": arg_name,
                    "description": description,
                })

            # --- .IP pattern ---
            elif macro == ".IP":
                # .IP "flag text" or .IP flag
                rest = stripped[3:].strip()

                # Handle continuation .IP (no arguments) — skip
                if not rest:
                    i += 1
                    continue

                # Extract the tag text (may be quoted)
                if rest.startswith('"'):
                    end_quote = rest.find('"', 1)
                    if end_quote > 0:
                        tag_text = rest[1:end_quote]
                    else:
                        tag_text = rest[1:]
                else:
                    # Take everything up to optional trailing width number
                    # e.g. ".IP \-P" → "\-P", ".IP \-P 4" → "\-P"
                    tag_text = rest.rstrip()
                    # Remove trailing number (indent width)
                    tag_text = re.sub(r'\s+\d+$', '', tag_text)

                parsed = _parse_flag_text(tag_text)
                if not parsed.get("short") and not parsed.get("long") and not parsed.get("argument"):
                    i += 1
                    continue

                i += 1

                # Collect description
                desc_lines = []
                rs_depth = 0
                while i < sec_end:
                    dl = lines[i].rstrip("\n").strip()
                    dm = dl.split()[0] if dl.split() else ""
                    # Track RS/RE depth — content inside nested RS blocks
                    # is part of this option's description
                    if dm == ".RS":
                        rs_depth += 1
                        i += 1
                        continue
                    if dm == ".RE":
                        rs_depth -= 1
                        if rs_depth < 0:
                            rs_depth = 0
                        i += 1
                        continue
                    # Only break on new-entry macros at top level (not inside RS blocks)
                    if rs_depth == 0:
                        if dm in (".TP", ".HP", ".SH", ".SS"):
                            break
                        if dm == ".IP":
                            ip_rest = dl[3:].strip()
                            if ip_rest:
                                break
                            desc_lines.append("")
                            i += 1
                            continue
                        if dm in (".PP", ".LP", ".P"):
                            break
                    if dm in (".PD", ".br", ".sp", ".fi", ".nf", ".nh",
                              ".hy", ".ad", ".na", ".in", ".ti"):
                        i += 1
                        continue
                    if dm in (".B", ".I", ".BI", ".BR", ".IR", ".RB", ".RI"):
                        text = dl[len(dm):].strip()
                        if text:
                            desc_lines.append(text)
                        i += 1
                        continue
                    if dl and not dl.startswith(".\\\""):
                        desc_lines.append(dl)
                    i += 1

                description = _clean_roff("\n".join(desc_lines))

                results.append({
                    "short": parsed.get("short", []),
                    "long": parsed.get("long", []),
                    "expects_arg": parsed.get("expects_arg", False),
                    "argument": parsed.get("argument"),
                    "description": description,
                })

            # --- .HP pattern ---
            elif macro == ".HP":
                i += 1
                # Next non-empty line is the tag
                while i < sec_end:
                    tag_line = lines[i].rstrip("\n").strip()
                    if tag_line and not tag_line.startswith(".PD"):
                        break
                    i += 1
                else:
                    break

                parsed = _parse_flag_text(tag_line)
                if not parsed.get("short") and not parsed.get("long"):
                    i += 1
                    continue

                i += 1

                # Collect description
                desc_lines = []
                while i < sec_end:
                    dl = lines[i].rstrip("\n").strip()
                    dm = dl.split()[0] if dl.split() else ""
                    if dm in (".TP", ".IP", ".HP", ".SH", ".SS", ".PP", ".LP", ".P"):
                        break
                    if dm in (".PD", ".br", ".sp"):
                        i += 1
                        continue
                    if dl and not dl.startswith(".\\\""):
                        desc_lines.append(dl)
                    i += 1

                description = _clean_roff("\n".join(desc_lines))
                results.append({
                    "short": parsed.get("short", []),
                    "long": parsed.get("long", []),
                    "expects_arg": parsed.get("expects_arg", False),
                    "argument": parsed.get("argument"),
                    "description": description,
                })

            # --- .PP + .RS/.RE pattern (git/DocBook style) ---
            elif macro in (".PP", ".LP", ".P"):
                # Look ahead: next non-empty line should be flag text,
                # followed by .RS
                j = i + 1
                flag_line = None
                while j < sec_end:
                    nl = lines[j].rstrip("\n").strip()
                    if nl and not nl.startswith(".\\\""):
                        flag_line = nl
                        break
                    j += 1

                if flag_line is None:
                    i += 1
                    continue

                # Check if an .RS follows after the flag line
                k = j + 1
                has_rs = False
                while k < sec_end:
                    rl = lines[k].rstrip("\n").strip()
                    if rl and not rl.startswith(".\\\""):
                        if rl.startswith(".RS"):
                            has_rs = True
                        break
                    k += 1

                if not has_rs:
                    i += 1
                    continue

                parsed = _parse_flag_text(flag_line)
                if not parsed.get("short") and not parsed.get("long") and not parsed.get("argument"):
                    i += 1
                    continue

                # Skip to after .RS
                i = k + 1

                # Collect description until .RE
                desc_lines = []
                rs_depth = 1
                while i < sec_end:
                    dl = lines[i].rstrip("\n").strip()
                    dm = dl.split()[0] if dl.split() else ""

                    if dm == ".RS":
                        rs_depth += 1
                        i += 1
                        continue
                    if dm == ".RE":
                        rs_depth -= 1
                        if rs_depth <= 0:
                            i += 1
                            break
                        i += 1
                        continue
                    if dm == ".SH":
                        break
                    if dm in (".PD", ".br", ".fi", ".nf", ".nh",
                              ".hy", ".ad", ".na", ".in", ".ti"):
                        i += 1
                        continue
                    if dm == ".sp":
                        desc_lines.append("")
                        i += 1
                        continue
                    if dm in (".PP", ".LP", ".P"):
                        desc_lines.append("")
                        i += 1
                        continue
                    if dm in (".B", ".I", ".BI", ".BR", ".IR", ".RB", ".RI"):
                        text = dl[len(dm):].strip()
                        if text:
                            desc_lines.append(text)
                        i += 1
                        continue
                    if dl and not dl.startswith(".\\\""):
                        desc_lines.append(dl)
                    i += 1

                description = _clean_roff("\n".join(desc_lines))
                results.append({
                    "short": parsed.get("short", []),
                    "long": parsed.get("long", []),
                    "expects_arg": parsed.get("expects_arg", False),
                    "argument": parsed.get("argument"),
                    "description": description,
                })

            # --- .SS subsection — continue parsing within it ---
            elif macro == ".SS":
                i += 1
                continue

            else:
                i += 1

    return results


# ---------------------------------------------------------------------------
# mdoc(7) parser — .It Fl pattern
# ---------------------------------------------------------------------------

def _find_option_sections_mdoc(lines: list) -> list:
    """Find line ranges for option-related sections in mdoc(7) pages.

    Returns list of (start_line, end_line) tuples.
    """
    sections = []
    current_start = None
    in_option_section = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith(".Sh "):
            if in_option_section and current_start is not None:
                sections.append((current_start, i))
                in_option_section = False

            section_name = stripped[4:].strip().strip('"')
            if _in_option_section(section_name):
                current_start = i + 1
                in_option_section = True

    if in_option_section and current_start is not None:
        sections.append((current_start, len(lines)))

    return sections


def _parse_mdoc_it_line(line: str) -> dict:
    """Parse an mdoc .It line to extract flags and arguments.

    Examples:
      .It Fl c
      .It Fl v , Fl -verbose
      .It Fl f Ar file
      .It Fl -file Ns = Ns Ar FILE
      .It Fl c , Fl -create
      .It Fl Fl create
    """
    # Remove .It prefix
    rest = line.strip()
    if rest.startswith(".It "):
        rest = rest[4:]
    else:
        return {}

    short = []
    long = []
    expects_arg = False
    argument = None

    # Tokenize the mdoc line
    tokens = rest.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok == "Fl":
            # Next token is the flag
            i += 1
            if i < len(tokens):
                flag = tokens[i]
                # .It Fl Fl long → --long (double Fl = long option)
                if flag == "Fl" or flag == "-":
                    i += 1
                    if i < len(tokens):
                        flag_name = tokens[i].replace("\\-", "-")
                        long.append("--" + flag_name)
                elif flag.startswith("\\-"):
                    # \-flag → long option (Fl \-create means --create)
                    flag_clean = flag.replace("\\-", "-")
                    # flag_clean is now "-create" or "-auto-compress"
                    long.append("-" + flag_clean)  # becomes "--create"
                elif flag.startswith("-"):
                    # Already has dash prefix
                    flag_clean = flag.replace("\\-", "-")
                    if flag_clean.startswith("--"):
                        long.append(flag_clean)
                    else:
                        long.append("-" + flag_clean)
                else:
                    flag_clean = flag.replace("\\-", "-")
                    if len(flag_clean) == 1:
                        short.append("-" + flag_clean)
                    else:
                        # Could be a long flag written as Fl long-name
                        long.append("--" + flag_clean)
        elif tok == "Ar":
            # Argument follows
            i += 1
            arg_parts = []
            while i < len(tokens) and tokens[i] not in ("Fl", ",", "Ns", "Op"):
                arg_parts.append(tokens[i])
                i += 1
            if arg_parts:
                expects_arg = True
                argument = " ".join(arg_parts)
            continue  # Don't increment i again
        elif tok in (",", "Ns", "|", "Cm"):
            # Separator or namespace — skip
            pass
        elif tok == "=" or tok == "\\=":
            # Equals sign between flag and arg
            pass
        elif tok in ("Op", "Oo", "Oc"):
            # Optional markers — skip
            pass
        else:
            # Could be a literal flag or other text
            pass

        i += 1

    return {
        "short": short,
        "long": long,
        "expects_arg": expects_arg,
        "argument": argument,
    }


def _clean_mdoc_text(text: str) -> str:
    """Clean mdoc-formatted description text."""
    # Remove common mdoc macros from body text
    text = re.sub(r"^\.(Pp|Pp)\s*$", "", text, flags=re.MULTILINE)
    # .Nm → command name (keep text after it or remove bare .Nm)
    text = re.sub(r"^\.Nm\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\.Nm\s+", "", text, flags=re.MULTILINE)
    # .Ar arg → arg
    text = re.sub(r"\.Ar\s+", "", text)
    # .Fl flag → -flag
    text = re.sub(r"\.Fl\s+", "-", text)
    # .Fl Fl flag → --flag
    text = re.sub(r"--\s+", "--", text)
    # .Cm text → text
    text = re.sub(r"\.Cm\s+", "", text)
    # .Em text → text
    text = re.sub(r"\.Em\s+", "", text)
    # .Xr name section → name(section)
    text = re.sub(r"\.Xr\s+(\S+)\s+(\d+)", r"\1(\2)", text)
    # .Dq text → "text"
    text = re.sub(r'\.Dq\s+"?([^"]*)"?', r'"\1"', text)
    # .Sq text → 'text'
    text = re.sub(r"\.Sq\s+'?([^']*)'?", r"'\1'", text)
    # .Pa path → path
    text = re.sub(r"\.Pa\s+", "", text)
    # .Ev var → var
    text = re.sub(r"\.Ev\s+", "", text)
    # .Va var → var
    text = re.sub(r"\.Va\s+", "", text)
    # .Sy text → text (symbolic/bold)
    text = re.sub(r"\.Sy\s+", "", text)
    # .No text → text
    text = re.sub(r"\.No\s+", "", text)
    # .Li text → text (literal)
    text = re.sub(r"\.Li\s+", "", text)
    # .Ns → join (no space)
    text = text.replace(" Ns ", "")
    text = text.replace(".Ns", "")
    # Remove .Pp paragraph markers
    text = re.sub(r"^\.Pp\s*$", "", text, flags=re.MULTILINE)
    # Remove .Bl, .El, .It sub-list markers
    text = re.sub(r"^\.(Bl|El)\s.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\.(Bl|El)\s*$", "", text, flags=re.MULTILINE)

    return _clean_roff(text)


def _parse_mdoc_options(lines: list) -> list:
    """Parse options from mdoc(7) format pages."""
    sections = _find_option_sections_mdoc(lines)
    if not sections:
        return []

    results = []

    for sec_start, sec_end in sections:
        # Find .Bl -tag blocks within this section
        i = sec_start
        in_list = False
        list_depth = 0

        while i < sec_end:
            stripped = lines[i].rstrip("\n").strip()

            if stripped.startswith(".Bl "):
                list_depth += 1
                if "-tag" in stripped:
                    in_list = True
                i += 1
                continue

            if stripped == ".El":
                list_depth -= 1
                if list_depth <= 0:
                    in_list = False
                    list_depth = 0
                i += 1
                continue

            if in_list and stripped.startswith(".It "):
                # Check if this .It line contains flags
                parsed = _parse_mdoc_it_line(stripped)

                if not parsed.get("short") and not parsed.get("long"):
                    i += 1
                    continue

                i += 1

                # Collect description until next .It or .El
                desc_lines = []
                while i < sec_end:
                    dl = lines[i].rstrip("\n").strip()
                    if dl.startswith(".It ") or dl == ".El":
                        break
                    if dl.startswith(".Bl "):
                        # Skip nested lists
                        nest = 1
                        i += 1
                        while i < sec_end and nest > 0:
                            ndl = lines[i].rstrip("\n").strip()
                            if ndl.startswith(".Bl "):
                                nest += 1
                            elif ndl == ".El":
                                nest -= 1
                            i += 1
                        continue
                    if dl and not dl.startswith(".\\\""):
                        desc_lines.append(dl)
                    i += 1

                description = _clean_mdoc_text("\n".join(desc_lines))

                results.append({
                    "short": parsed.get("short", []),
                    "long": parsed.get("long", []),
                    "expects_arg": parsed.get("expects_arg", False),
                    "argument": parsed.get("argument"),
                    "description": description,
                })
            else:
                i += 1

    return results
