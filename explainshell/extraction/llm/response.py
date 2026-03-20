"""JSON parsing, validation, option conversion, and chunk-level dedup."""

from __future__ import annotations

import json
import logging
import re

from explainshell import models
from explainshell.errors import ExtractionError

logger = logging.getLogger(__name__)


def fix_invalid_escapes(s: str) -> str:
    """Replace invalid JSON escape sequences with their escaped form.

    JSON only allows: \\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX.
    LLMs sometimes produce things like \\p or \\a which are invalid.
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", s)


def parse_json_response(content: str) -> dict:
    """Strip markdown fences, find outermost {…}, parse JSON."""
    content = re.sub(r"^```[^\n]*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content.strip())

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ExtractionError(
            f"No JSON object found in LLM response: {content[:200]!r}",
            raw_response=content,
        )

    raw = content[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(fix_invalid_escapes(raw))
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Invalid JSON from LLM: {e}", raw_response=content
        ) from e


def validate_llm_response(data: dict) -> None:
    """Raises ValueError if data is missing 'options' or options have wrong types."""
    if "options" not in data:
        raise ValueError("LLM response missing 'options' key")
    if not isinstance(data["options"], list):
        raise ValueError("'options' must be a list")
    for item in data["options"]:
        if not isinstance(item, dict):
            raise ValueError(f"Each option must be a dict, got {type(item)}")


def process_llm_result(content: str) -> tuple[dict, str]:
    """Parse and validate a raw LLM response string.

    Returns (data_dict, raw_content).
    Raises ExtractionError on parse failure or validation failure.
    """
    data = parse_json_response(content)
    try:
        validate_llm_response(data)
    except ValueError as e:
        raise ExtractionError(str(e), raw_response=content) from e
    return data, content


def extract_text_from_lines(
    original_lines: dict[int, str], start: int, end: int
) -> str:
    """Build description text from line range [start, end] (1-indexed, inclusive).

    The first line is treated as the flag line. Remaining lines form the
    description body. Blockquote prefixes ("> ") are stripped from all lines.
    """
    if start < 1 or end < start:
        return ""
    selected: list[str] = []
    for i in range(start, end + 1):
        line = original_lines.get(i, "")
        if line.startswith("> "):
            line = line[2:]
        selected.append(line)

    if not selected:
        return ""

    flag_line = selected[0]
    body_lines = selected[1:]

    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    if body_lines:
        return flag_line + "\n\n" + "\n".join(body_lines)
    return flag_line


def sanitize_option_fields(
    short: list[str],
    long: list[str],
    has_argument: bool | list[str],
    positional: str | None,
    nested_cmd: bool,
) -> tuple[list[str], list[str], bool | list[str], str | None, bool]:
    """Fix common LLM mistakes in option fields.

    Returns (short, long, has_argument, positional, nested_cmd).
    """
    if positional and (short or long):
        logger.debug(
            "clearing positional=%r on flagged option %s/%s", positional, short, long
        )
        positional = None

    if nested_cmd and not has_argument:
        has_argument = True

    return short, long, has_argument, positional, nested_cmd


def llm_option_to_store_option(
    raw: dict, original_lines: dict[int, str]
) -> models.Option:
    """Convert one LLM option dict to a models.Option.

    Uses the "lines" field to slice the description from original_lines.
    """
    short = raw.get("short") or []
    long = raw.get("long") or []
    has_argument = raw.get("has_argument", False)
    positional = raw.get("positional") or None
    nested_cmd = bool(raw.get("nested_cmd", False))

    if not isinstance(short, list):
        raise ValueError(f"'short' must be a list, got {type(short)}")
    if not isinstance(long, list):
        raise ValueError(f"'long' must be a list, got {type(long)}")

    lines = raw.get("lines")
    if not lines or not isinstance(lines, list) or len(lines) != 2:
        raise ValueError(f"'lines' must be a [start, end] list, got {lines!r}")
    start, end = int(lines[0]), int(lines[1])
    text = extract_text_from_lines(original_lines, start, end)

    short, long, has_argument, positional, nested_cmd = sanitize_option_fields(
        short, long, has_argument, positional, nested_cmd
    )

    return models.Option(
        text=text,
        short=short,
        long=long,
        has_argument=has_argument,
        positional=positional,
        nested_cmd=nested_cmd,
        meta={"lines": [start, end]},
    )


def dedup_options(raw_options: list[dict]) -> list[dict]:
    """Remove options with duplicate (short+long) flag sets (from chunk overlap).

    When duplicates exist, keep the entry with the longest description so that
    detailed sections win over brief summary entries.
    """
    best: dict[tuple, tuple[int, dict]] = {}
    positional: list[tuple[int, dict]] = []
    for idx, opt in enumerate(raw_options):
        try:
            raw_short = opt.get("short") or []
            raw_long = opt.get("long") or []
            if not isinstance(raw_short, list) or not isinstance(raw_long, list):
                positional.append((idx, opt))
                continue
            short = tuple(sorted(str(s) for s in raw_short))
            long = tuple(sorted(str(s) for s in raw_long))
        except (TypeError, AttributeError):
            positional.append((idx, opt))
            continue
        key = (short, long)
        if not short and not long:
            positional.append((idx, opt))
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = (idx, opt)
        else:
            old_desc = len(prev[1].get("description") or "")
            new_desc = len(opt.get("description") or "")
            if new_desc > old_desc:
                best[key] = (idx, opt)
    all_entries = list(best.values()) + positional
    all_entries.sort(key=lambda x: x[0])
    return [opt for _, opt in all_entries]


def dedup_ref_options(raw_options: list[dict]) -> list[dict]:
    """Dedup options using synthetic description length based on line span.

    This wraps dedup_options by injecting a synthetic "description" key
    so that the longest-span entry wins during dedup.
    """
    for opt in raw_options:
        lines = opt.get("lines")
        if lines and isinstance(lines, list) and len(lines) == 2:
            opt["description"] = "x" * (int(lines[1]) - int(lines[0]) + 1)
        elif "description" not in opt:
            opt["description"] = ""
    return dedup_options(raw_options)
