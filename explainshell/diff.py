"""Man page comparison and diff formatting.

Moved from manager.py to be usable by regression tests and other consumers.
"""

from __future__ import annotations

import difflib

from explainshell.models import ParsedManpage

# ParsedManpage-level fields to compare in diff mode.
_MP_FIELDS = (
    "name",
    "synopsis",
    "aliases",
    "nested_cmd",
    "has_subcommands",
    "dashless_opts",
    "extractor",
    "extraction_meta",
)

# Per-option fields to compare in diff mode.
_OPT_FIELDS = ("has_argument", "positional", "nested_cmd", "text")

# Fields where None and False should be treated as equivalent.
_FALSY_EQUIVALENT = {"nested_cmd", "positional"}

# ANSI color helpers.
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _normalize(field: str, val: object) -> object:
    if field in _FALSY_EQUIVALENT and not val:
        return None
    if field == "aliases" and isinstance(val, list):
        return sorted(val)
    return val


def _option_key(opt: object) -> tuple:
    if opt.short or opt.long:  # type: ignore[union-attr]
        return (tuple(sorted(opt.short)), tuple(sorted(opt.long)))  # type: ignore[union-attr]
    return ("positional", opt.positional)  # type: ignore[union-attr]


def _fmt_flags(opt: object) -> str:
    parts = list(opt.short) + list(opt.long)  # type: ignore[union-attr]
    if not parts:
        return f"(positional: {opt.positional})"  # type: ignore[union-attr]
    return "[" + ", ".join(parts) + "]"


def compare_manpages(
    stored_mp: ParsedManpage,
    fresh_mp: ParsedManpage,
    skip_fields: tuple[str, ...] = (),
) -> list[dict]:
    """Compare two ParsedManpage objects and return a list of structured diff entries.

    Each entry is a dict with:
      - "type": "field" | "option_changed" | "option_added" | "option_removed"
      - "label": human-readable label
      - "details": list of (field, old_val, new_val) tuples (for field/option_changed)
                   or option object (for added/removed)

    *skip_fields* is an optional iterable of top-level field names to ignore.
    """
    diffs: list[dict] = []
    skip = set(skip_fields)

    for field in _MP_FIELDS:
        if field in skip:
            continue
        old_val = _normalize(field, getattr(stored_mp, field))
        new_val = _normalize(field, getattr(fresh_mp, field))
        if old_val != new_val:
            diffs.append(
                {
                    "type": "field",
                    "label": field,
                    "details": [(field, old_val, new_val)],
                }
            )

    stored_opts = {_option_key(o): o for o in stored_mp.options}
    fresh_opts = {_option_key(o): o for o in fresh_mp.options}
    all_keys = list(dict.fromkeys(list(stored_opts.keys()) + list(fresh_opts.keys())))

    for key in all_keys:
        s_opt = stored_opts.get(key)
        f_opt = fresh_opts.get(key)

        if s_opt and f_opt:
            opt_diffs = []
            for field in _OPT_FIELDS:
                old_val = _normalize(field, getattr(s_opt, field))
                new_val = _normalize(field, getattr(f_opt, field))
                if old_val != new_val:
                    opt_diffs.append((field, old_val, new_val))
            if opt_diffs:
                diffs.append(
                    {
                        "type": "option_changed",
                        "label": _fmt_flags(s_opt),
                        "details": opt_diffs,
                    }
                )
        elif f_opt:
            diffs.append(
                {
                    "type": "option_added",
                    "label": _fmt_flags(f_opt),
                    "details": f_opt,
                }
            )
        else:
            diffs.append(
                {
                    "type": "option_removed",
                    "label": _fmt_flags(s_opt),
                    "details": s_opt,
                }
            )

    return diffs


def _fmt_value(val: object, indent: str, color: str) -> str:
    s = str(val)
    lines = s.split("\n")
    if len(lines) == 1:
        return f"{color}{indent}{s}{_RESET}"
    out = [f"{color}{indent}{lines[0]}"]
    for line in lines[1:]:
        out.append(f"{indent}  {line}")
    out[-1] += _RESET
    return "\n".join(out)


def _fmt_text_diff(old_text: object, new_text: object, indent: str) -> str | None:
    old_lines = str(old_text).splitlines(keepends=True)
    new_lines = str(new_text).splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, n=1))
    if not diff:
        return None
    out: list[str] = []
    for line in diff[2:]:
        if line.startswith("@@"):
            continue
        text = line[1:].rstrip("\n")
        if line.startswith("-"):
            out.append(f"{_RED}{indent}- {text}{_RESET}")
        elif line.startswith("+"):
            out.append(f"{_GREEN}{indent}+ {text}{_RESET}")
        else:
            out.append(f"{_DIM}{indent}  {text}{_RESET}")
    blank = f"{_DIM}{indent}  {_RESET}"
    while out and out[0] == blank:
        out.pop(0)
    while out and out[-1] == blank:
        out.pop()
    return "\n".join(out)


def _option_detail_lines(opt: object, prefix: str = "", color: str = "") -> list[str]:
    lines: list[str] = []
    lines.append(f"{color}{prefix}    short: {opt.short}")  # type: ignore[union-attr]
    lines.append(f"{prefix}    long: {opt.long}")  # type: ignore[union-attr]
    lines.append(f"{prefix}    has_argument: {opt.has_argument}")  # type: ignore[union-attr]
    if opt.positional:  # type: ignore[union-attr]
        lines.append(f"{prefix}    positional: {opt.positional}")  # type: ignore[union-attr]
    if opt.nested_cmd:  # type: ignore[union-attr]
        lines.append(f"{prefix}    nested_cmd: {opt.nested_cmd}")  # type: ignore[union-attr]
    desc = opt.text.strip()  # type: ignore[union-attr]
    for line in desc.split("\n"):
        lines.append(f"{prefix}    {line}")
    lines.append(_RESET)
    return lines


def format_diff(stored_mp: ParsedManpage, fresh_mp: ParsedManpage) -> list[str]:
    """Return a list of lines with a unified-diff-style comparison."""
    diffs = compare_manpages(stored_mp, fresh_mp)
    out: list[str] = []

    field_diffs = [d for d in diffs if d["type"] == "field"]

    for d in field_diffs:
        field = d["label"]
        _, old_val, new_val = d["details"][0]
        out.append(f"  {_BOLD}{field}:{_RESET}")
        text_diff = _fmt_text_diff(old_val, new_val, "    ")
        if text_diff:
            out.append(text_diff)
        else:
            out.append(_fmt_value(old_val, "    - ", _RED))
            out.append(_fmt_value(new_val, "    + ", _GREEN))

    stored_opts = {_option_key(o): o for o in stored_mp.options}
    fresh_opts = {_option_key(o): o for o in fresh_mp.options}
    all_keys = list(dict.fromkeys(list(stored_opts.keys()) + list(fresh_opts.keys())))

    changed_options: list[tuple[str, list | None]] = []
    added_options: list[object] = []
    removed_options: list[object] = []

    for key in all_keys:
        s_opt = stored_opts.get(key)
        f_opt = fresh_opts.get(key)
        if s_opt and f_opt:
            opt_diffs = []
            for field in _OPT_FIELDS:
                old_val = _normalize(field, getattr(s_opt, field))
                new_val = _normalize(field, getattr(f_opt, field))
                if old_val != new_val:
                    opt_diffs.append((field, old_val, new_val))
            changed_options.append(
                (_fmt_flags(s_opt), opt_diffs if opt_diffs else None)
            )
        elif f_opt:
            added_options.append(f_opt)
        else:
            removed_options.append(s_opt)

    if changed_options or added_options or removed_options:
        out.append(f"  {_BOLD}options:{_RESET}")

    for label, opt_field_diffs in changed_options:
        if opt_field_diffs is None:
            out.append(f"    {_DIM}{label}  (unchanged){_RESET}")
        else:
            out.append(f"    {_CYAN}{_BOLD}{label}{_RESET}")
            for field, old_val, new_val in opt_field_diffs:
                out.append(f"      {field}:")
                text_diff = _fmt_text_diff(old_val, new_val, "        ")
                if text_diff:
                    out.append(text_diff)
                else:
                    out.append(_fmt_value(old_val, "        - ", _RED))
                    out.append(_fmt_value(new_val, "        + ", _GREEN))

    for opt in added_options:
        out.append(f"    {_GREEN}{_BOLD}+ {_fmt_flags(opt)}   (added){_RESET}")
        out.extend(_option_detail_lines(opt, prefix="    ", color=_GREEN))

    for opt in removed_options:
        out.append(f"    {_RED}{_BOLD}- {_fmt_flags(opt)}   (removed){_RESET}")
        out.extend(_option_detail_lines(opt, prefix="    ", color=_RED))

    if not diffs:
        out.append(f"  {_DIM}(no changes){_RESET}")

    return out
