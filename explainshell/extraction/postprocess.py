"""Extractor-agnostic post-processing pipeline for options."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from explainshell.store import Option

logger = logging.getLogger(__name__)


@dataclass
class PostProcessStats:
    malformed_options: int = 0
    deduped_options: int = 0
    blank_description_stripped: int = 0


def sanitize_option(opt: Option) -> Option:
    """Fix universally-invalid field combinations.

    - positional + flags: clear positional
    - nested_cmd without has_argument: set has_argument = True
    """
    positional = opt.positional
    has_argument = opt.has_argument
    changed = False

    if positional and (opt.short or opt.long):
        logger.debug(
            "clearing positional=%r on flagged option %s/%s",
            positional,
            opt.short,
            opt.long,
        )
        positional = None
        changed = True

    if opt.nested_cmd and not has_argument:
        has_argument = True
        changed = True

    if not changed:
        return opt

    return Option(
        text=opt.text,
        short=opt.short,
        long=opt.long,
        has_argument=has_argument,
        positional=positional,
        nested_cmd=opt.nested_cmd,
        meta=opt.meta,
    )


def strip_trailing_blanks(opt: Option) -> Option:
    """Remove trailing blank lines from description text."""
    stripped = opt.text.rstrip("\n ")
    if stripped == opt.text:
        return opt
    return Option(
        text=stripped,
        short=opt.short,
        long=opt.long,
        has_argument=opt.has_argument,
        positional=opt.positional,
        nested_cmd=opt.nested_cmd,
        meta=opt.meta,
    )


def dedup_options(options: list[Option]) -> tuple[list[Option], int]:
    """Remove options with identical flag sets, keeping longest description.

    Returns (deduped_list, num_removed).
    """
    best: dict[tuple, tuple[int, Option]] = {}
    positional: list[tuple[int, Option]] = []

    for idx, opt in enumerate(options):
        if not opt.short and not opt.long:
            positional.append((idx, opt))
            continue
        key = (tuple(sorted(opt.short)), tuple(sorted(opt.long)))
        prev = best.get(key)
        if prev is None:
            best[key] = (idx, opt)
        else:
            old_len = len(prev[1].text)
            new_len = len(opt.text)
            if new_len > old_len:
                best[key] = (idx, opt)

    all_entries = list(best.values()) + positional
    all_entries.sort(key=lambda x: x[0])
    result = [opt for _, opt in all_entries]
    return result, len(options) - len(result)


def drop_empty(options: list[Option]) -> tuple[list[Option], int]:
    """Remove options with no flags and no positional name.

    Returns (filtered_list, num_removed).
    """
    kept = [opt for opt in options if opt.short or opt.long or opt.positional]
    return kept, len(options) - len(kept)


def postprocess(
    options: list[Option],
    steps: list[str] | None = None,
) -> tuple[list[Option], PostProcessStats]:
    """Run selected post-processing steps.

    steps=None means all. Otherwise a subset like
    ["sanitize", "dedup", "strip_blanks", "drop_empty"].
    """
    all_steps = {"sanitize", "dedup", "strip_blanks", "drop_empty"}
    active = set(steps) if steps is not None else all_steps
    stats = PostProcessStats()

    if "sanitize" in active:
        options = [sanitize_option(opt) for opt in options]

    if "strip_blanks" in active:
        new_opts = []
        for opt in options:
            stripped = strip_trailing_blanks(opt)
            if stripped is not opt:
                stats.blank_description_stripped += 1
            new_opts.append(stripped)
        options = new_opts

    if "dedup" in active:
        options, removed = dedup_options(options)
        stats.deduped_options = removed

    if "drop_empty" in active:
        options, removed = drop_empty(options)
        stats.malformed_options = removed

    return options, stats
