"""Extractor-agnostic post-processing pipeline for options."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from explainshell.errors import ExtractionError
from explainshell.models import Option

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sanity thresholds for LLM line-span validation
# ---------------------------------------------------------------------------
# Maximum ratio of sum-of-all-option-spans to total document lines.
# Good extractions sit below ~3x; the 5–10x band is uniformly botched
# (overlapping near-document-sized spans).  A few borderline cases
# (e.g. nmap at 3.8x) slip under this threshold but lowering further
# would false-positive on ImageMagick and similar pages whose mandoc
# output has very few but very long lines.
_MAX_COVERAGE_RATIO = 3.0


@dataclass
class PostProcessStats:
    dropped_empty: int = 0
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


def _subset_has_cross_reference(subset: Option, extra_flags: frozenset[str]) -> bool:
    """Check if subset's description mentions any of the superset's extra flags.

    This guards against false-positive subset merges that occur when the LLM
    normalises dashless BSD options (e.g. ``U`` → ``-U``), creating spurious
    flag overlaps with genuinely different POSIX options (e.g. ``-U``/``--User``).
    Legitimate duplicates typically cross-reference the other entry's flags
    ("Identical to -M", "same as --sort").

    Flags are matched with non-alphanumeric boundaries so that bare
    single-letter flags like ``k`` do not match inside ordinary words
    ("work", "make") and flag prefixes like ``-M`` do not match inside
    longer tokens.
    """
    desc = subset.text
    for flag in extra_flags:
        pattern = r"(?<![a-zA-Z0-9\-])" + re.escape(flag) + r"(?![a-zA-Z0-9\-])"
        if re.search(pattern, desc):
            return True
    return False


def dedup_options(options: list[Option]) -> tuple[list[Option], int]:
    """Remove options whose flag set is a subset of (or equal to) another's.

    Covers both strict-subset (e.g. {Z} vs {-M, Z}) and exact-match
    duplicates (e.g. two entries for {O}).  When one option's combined flags
    (short ∪ long) are ⊆ another's, the smaller entry is removed.

    For **exact-match** duplicates the first occurrence's position is kept.
    If a later duplicate has a longer description, the first entry is
    replaced with the later entry entirely (all fields, not just text).

    For **strict subsets** the superset entry survives with its own flags.
    If the subset has a longer description, the text and ``meta`` are
    transferred so that provenance (e.g. ``meta["lines"]``) stays
    consistent with the description text.

    When multiple supersets qualify, the **closest** one (smallest number
    of extra flags) is chosen so the subset merges into the most specific
    match rather than depending on input order.

    For **strict** subsets an additional cross-reference check is applied:
    the subset's description must mention at least one of the superset's
    extra flags.  This prevents false merges where dashless-option
    normalisation creates spurious flag overlaps (see ``_subset_has_cross_reference``).

    Returns (deduped_list, num_removed).
    """
    flags_list: list[frozenset[str]] = []
    for opt in options:
        if opt.short or opt.long:
            flags_list.append(frozenset(opt.short + opt.long))
        else:
            flags_list.append(frozenset())

    removed: set[int] = set()

    # Pass 1: exact-match dedup.  First occurrence's position wins;
    # if a later duplicate has a longer description, replace entirely.
    seen: dict[frozenset[str], int] = {}
    for i in range(len(options)):
        fi = flags_list[i]
        if not fi:
            continue
        if fi in seen:
            first = seen[fi]
            if len(options[i].text) > len(options[first].text):
                options[first] = options[i]
            removed.add(i)
        else:
            seen[fi] = i

    # Pass 2: strict-subset dedup.  For each surviving option, search
    # ALL other surviving options (bidirectional) for the closest
    # qualifying superset.
    for i in range(len(options)):
        if i in removed or not flags_list[i]:
            continue
        fi = flags_list[i]
        best_j: int | None = None
        best_extra_len: int = 0
        for j in range(len(options)):
            if j == i or j in removed or not flags_list[j]:
                continue
            fj = flags_list[j]
            if fi < fj and _subset_has_cross_reference(options[i], fj - fi):
                extra = len(fj - fi)
                if best_j is None or extra < best_extra_len:
                    best_j = j
                    best_extra_len = extra
        if best_j is not None:
            sup = options[best_j]
            sub = options[i]
            if len(sub.text) > len(sup.text):
                options[best_j] = Option(
                    text=sub.text,
                    short=sup.short,
                    long=sup.long,
                    has_argument=sup.has_argument,
                    positional=sup.positional,
                    nested_cmd=sup.nested_cmd,
                    meta=sub.meta,
                )
            removed.add(i)

    result = [opt for idx, opt in enumerate(options) if idx not in removed]
    return result, len(removed)


def drop_empty(options: list[Option]) -> tuple[list[Option], int]:
    """Remove options with no flags and no positional name.

    Returns (filtered_list, num_removed).
    """
    kept = [opt for opt in options if opt.short or opt.long or opt.positional]
    return kept, len(options) - len(kept)


def sanity_check_line_spans(options: list[Option]) -> None:
    """Reject an LLM extraction whose line spans look pathological.

    Raises ``ExtractionError`` when the sum of all option spans exceeds
    ``_MAX_COVERAGE_RATIO`` times the document extent (derived from the
    highest end-line in the options' own metadata).  This catches the
    common failure mode where the LLM returns near-document-sized ranges
    (e.g. ``[1, 927]``) for every option instead of precise per-option
    spans.
    """
    if not options:
        return

    total_span = 0
    max_end = 0
    for opt in options:
        meta = opt.meta or {}
        lines = meta.get("lines")
        if lines and isinstance(lines, list) and len(lines) == 2:
            total_span += max(lines[1] - lines[0] + 1, 0)
            max_end = max(max_end, lines[1])

    if max_end < 1:
        return

    coverage = total_span / max_end

    if coverage > _MAX_COVERAGE_RATIO:
        raise ExtractionError(
            f"line-span coverage {coverage:.1f}x exceeds {_MAX_COVERAGE_RATIO}x limit "
            f"({len(options)} options, {max_end} lines) "
            f"(try a stronger model?)"
        )


def postprocess(
    options: list[Option],
    steps: list[str] | None = None,
) -> tuple[list[Option], PostProcessStats]:
    """Run selected post-processing steps.

    steps=None means all. Otherwise a subset like
    ["sanitize", "dedup", "strip_blanks", "drop_empty", "sanity_check_spans"].
    """
    all_steps = {
        "sanitize",
        "dedup",
        "strip_blanks",
        "drop_empty",
        "sanity_check_spans",
    }
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
        stats.dropped_empty = removed

    if "sanity_check_spans" in active:
        sanity_check_line_spans(options)

    return options, stats
