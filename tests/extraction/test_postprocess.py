"""Tests for explainshell.extraction.postprocess."""

from __future__ import annotations

import pytest

from explainshell.errors import ExtractionError
from explainshell.extraction.postprocess import (
    _subset_has_cross_reference,
    dedup_options,
    postprocess,
    sanity_check_line_spans,
)
from explainshell.models import Option


def _opt(
    text: str = "",
    short: list[str] | None = None,
    long: list[str] | None = None,
    has_argument: bool = False,
) -> Option:
    return Option(
        text=text, short=short or [], long=long or [], has_argument=has_argument
    )


class TestDedupOptions:
    """Unified dedup: removes both exact-match and subset duplicates."""

    # --- strict subset with cross-reference ---

    def test_short_subset_with_cross_ref(self) -> None:
        """Case 1 from ps: Z ⊂ {-M, Z} — subset mentions '-M'."""
        superset = _opt("Add security data. Identical to Z.", short=["-M", "Z"])
        subset = _opt("Add security data. Identical to -M.", short=["Z"])
        result, removed = dedup_options([superset, subset])
        assert removed == 1
        assert len(result) == 1
        assert result[0].short == ["-M", "Z"]

    def test_long_subset_with_cross_ref(self) -> None:
        """Case 2 from ps: --sort ⊂ {k, --sort} — subset mentions 'k'."""
        superset = _opt(
            "Specify sorting order. Identical to --sort.",
            short=["k"],
            long=["--sort"],
        )
        subset = _opt("Specify sorting order. Identical to k.", long=["--sort"])
        result, removed = dedup_options([superset, subset])
        assert removed == 1
        assert len(result) == 1
        assert result[0].short == ["k"]
        assert result[0].long == ["--sort"]

    def test_subset_keeps_longer_description(self) -> None:
        """When the subset entry has a longer description, transfer it."""
        superset = _opt("short", short=["-a", "-b"])
        subset = _opt("much longer description mentioning -b here", short=["-a"])
        result, removed = dedup_options([superset, subset])
        assert removed == 1
        assert result[0].text == "much longer description mentioning -b here"
        assert result[0].short == ["-a", "-b"]

    def test_subset_reversed_order(self) -> None:
        """Subset appears before superset in the list."""
        subset = _opt("Identical to -M.", short=["Z"])
        superset = _opt("desc", short=["-M", "Z"])
        result, removed = dedup_options([subset, superset])
        assert removed == 1
        assert len(result) == 1
        assert result[0].short == ["-M", "Z"]

    def test_subset_preserves_superset_fields(self) -> None:
        """Strict subset keeps superset's non-text fields even when
        subset has longer text."""
        superset = _opt("short", short=["-a", "-b"], has_argument=True)
        subset = _opt(
            "longer description that mentions -b explicitly",
            short=["-a"],
            has_argument=False,
        )
        result, removed = dedup_options([superset, subset])
        assert removed == 1
        assert result[0].text == "longer description that mentions -b explicitly"
        assert result[0].has_argument is True  # from superset

    def test_multiple_subsets_keep_longest_description(self) -> None:
        """When two subsets both cross-ref the superset, the longest text wins."""
        superset = _opt("short", short=["-a", "-b", "-c"])
        subset_long = _opt(
            "the longest description mentioning -b and -c",
            short=["-a"],
        )
        subset_short = _opt("mentions -b", short=["-a", "-c"])
        result, removed = dedup_options([superset, subset_long, subset_short])
        assert removed == 2
        assert len(result) == 1
        assert result[0].text == "the longest description mentioning -b and -c"
        assert result[0].short == ["-a", "-b", "-c"]

    def test_subset_picks_closest_superset(self) -> None:
        """With competing supersets, merge into the closest (fewest extra flags)."""
        # Distant superset appears first — should NOT win.
        distant = _opt("distant", short=["-a", "-b", "-c"])
        close = _opt("close", short=["-a", "-b"])
        subset = _opt("long subset text, Identical to -b.", short=["-a"])
        result, removed = dedup_options([distant, close, subset])
        assert removed == 1
        assert len(result) == 2
        # close ({-a, -b}) receives subset text; distant keeps its own
        by_flags = {frozenset(r.short + r.long): r for r in result}
        assert by_flags[frozenset({"-a", "-b"})].text == (
            "long subset text, Identical to -b."
        )
        assert by_flags[frozenset({"-a", "-b", "-c"})].text == "distant"

    def test_subset_picks_closest_superset_reversed(self) -> None:
        """Same as above but close superset appears first — same result."""
        close = _opt("close", short=["-a", "-b"])
        distant = _opt("distant", short=["-a", "-b", "-c"])
        subset = _opt("long subset text, Identical to -b.", short=["-a"])
        result, removed = dedup_options([close, distant, subset])
        assert removed == 1
        assert len(result) == 2
        by_flags = {frozenset(r.short + r.long): r for r in result}
        assert by_flags[frozenset({"-a", "-b"})].text == (
            "long subset text, Identical to -b."
        )
        assert by_flags[frozenset({"-a", "-b", "-c"})].text == "distant"

    def test_subset_text_transfer_carries_meta(self) -> None:
        """When subset text wins, its meta is carried to the superset."""
        superset = Option(
            text="short",
            short=["-a", "-b"],
            meta={"lines": [100, 105]},
        )
        subset = Option(
            text="longer description, Identical to -b.",
            short=["-a"],
            meta={"lines": [200, 220]},
        )
        result, removed = dedup_options([superset, subset])
        assert removed == 1
        assert result[0].text == "longer description, Identical to -b."
        assert result[0].meta == {"lines": [200, 220]}

    # --- strict subset WITHOUT cross-reference (should NOT merge) ---

    def test_subset_no_cross_ref_not_merged(self) -> None:
        """False positive guard: no mention of extra flags -> skip merge.

        Prevents merges like ps ``e`` (show env) ⊂ ``{-A, -e}`` (select all)
        where dashless normalisation creates spurious overlap.
        """
        superset = _opt("Select all processes.", short=["-A", "-e"])
        subset = _opt("Show the environment after the command.", short=["-e"])
        result, removed = dedup_options([superset, subset])
        assert removed == 0
        assert len(result) == 2

    def test_subset_no_cross_ref_long_flags(self) -> None:
        """Subset with long flag, no cross-ref -> not merged."""
        superset = _opt("Session selection.", short=["-s"], long=["--sid"])
        subset = _opt("Display signal format.", short=["-s"])
        result, removed = dedup_options([superset, subset])
        assert removed == 0
        assert len(result) == 2

    # --- exact-match cases (no cross-ref needed) ---

    def test_exact_match_removed(self) -> None:
        """Identical flag sets — longer description kept at first position."""
        a = _opt("desc a", short=["O"])
        b = _opt("longer desc b", short=["O"])
        result, removed = dedup_options([a, b])
        assert removed == 1
        assert len(result) == 1
        assert result[0].text == "longer desc b"
        assert result[0].short == ["O"]

    def test_exact_match_same_length_keeps_first(self) -> None:
        a = _opt("desc", short=["-x"])
        b = _opt("desc", short=["-x"])
        result, removed = dedup_options([a, b])
        assert removed == 1
        assert len(result) == 1

    def test_exact_match_first_occurrence_position(self) -> None:
        """With 3+ duplicates, the survivor appears at the first position."""
        a = _opt("short", short=["-x"])
        other = _opt("unrelated", short=["-y"])
        b = _opt("medium text", short=["-x"])
        c = _opt("the longest description here", short=["-x"])
        result, removed = dedup_options([a, other, b, c])
        assert removed == 2
        assert len(result) == 2
        # First entry should be the -x survivor (at position 0), then -y
        assert result[0].short == ["-x"]
        assert result[0].text == "the longest description here"
        assert result[1].short == ["-y"]

    def test_exact_match_preserves_all_fields(self) -> None:
        """When a later duplicate has longer text, all its fields are kept."""
        a = _opt("short", short=["-x"], has_argument=False)
        b = Option(
            text="longer description",
            short=["-x"],
            has_argument=True,
            meta={"lines": [10, 20]},
        )
        result, removed = dedup_options([a, b])
        assert removed == 1
        assert result[0].text == "longer description"
        assert result[0].has_argument is True
        assert result[0].meta == {"lines": [10, 20]}

    # --- no-op cases ---

    def test_disjoint_overlap_not_merged(self) -> None:
        """Overlapping but non-subset flags: neither is removed."""
        a = _opt("desc", short=["-a"], long=["--foo"])
        b = _opt("desc", long=["--foo", "--bar"])
        result, removed = dedup_options([a, b])
        assert removed == 0
        assert len(result) == 2

    def test_positionals_skipped(self) -> None:
        """Positional-only options (no flags) are never deduped."""
        pos = Option(text="file", positional="FILE")
        flagged = _opt("some opt", short=["-f"])
        result, removed = dedup_options([pos, flagged])
        assert removed == 0
        assert len(result) == 2

    def test_no_options(self) -> None:
        result, removed = dedup_options([])
        assert removed == 0
        assert result == []

    def test_single_option(self) -> None:
        opt = _opt("desc", short=["-a"])
        result, removed = dedup_options([opt])
        assert removed == 0
        assert len(result) == 1


class TestSubsetCrossReference:
    """Word-boundary matching in _subset_has_cross_reference."""

    def test_single_char_flag_not_in_prose(self) -> None:
        """Bare 'k' must not match inside words like 'work' or 'make'."""
        opt = _opt("This option does work and makes things check out.")
        assert not _subset_has_cross_reference(opt, frozenset({"k"}))

    def test_single_char_flag_standalone(self) -> None:
        """Bare 'k' as a standalone token does match."""
        opt = _opt("Identical to k.")
        assert _subset_has_cross_reference(opt, frozenset({"k"}))

    def test_dash_flag_not_in_longer_token(self) -> None:
        """-M must not match inside --Macro or re-Make."""
        opt = _opt("See --Macro-definitions for details.")
        assert not _subset_has_cross_reference(opt, frozenset({"-M"}))

    def test_dash_flag_standalone(self) -> None:
        opt = _opt("Identical to -M (for SELinux).")
        assert _subset_has_cross_reference(opt, frozenset({"-M"}))

    def test_long_flag_not_partial(self) -> None:
        """--sort must not match inside --sort-order."""
        opt = _opt("Use --sort-order to change ordering.")
        assert not _subset_has_cross_reference(opt, frozenset({"--sort"}))

    def test_long_flag_standalone(self) -> None:
        opt = _opt("Identical to --sort.")
        assert _subset_has_cross_reference(opt, frozenset({"--sort"}))


class TestPostprocessDedupIntegration:
    """Dedup is wired into the postprocess pipeline."""

    def test_subset_dedup_runs_by_default(self) -> None:
        superset = _opt("desc", short=["-M", "Z"])
        subset = _opt("Identical to -M.", short=["Z"])
        result, stats = postprocess([superset, subset])
        assert len(result) == 1
        assert stats.deduped_options == 1

    def test_subset_and_exact_dedup_combined(self) -> None:
        """Both subset and exact-match are handled in one pass."""
        superset = _opt("desc", short=["-M", "Z"])
        subset = _opt("Identical to -M.", short=["Z"])
        exact_a = _opt("desc a", short=["O"])
        exact_b = _opt("longer desc b", short=["O"])
        result, stats = postprocess([superset, subset, exact_a, exact_b])
        assert len(result) == 2
        assert stats.deduped_options == 2

    def test_dedup_step_can_be_skipped(self) -> None:
        superset = _opt("desc", short=["-M", "Z"])
        subset = _opt("Identical to -M.", short=["Z"])
        result, stats = postprocess([superset, subset], steps=["sanitize"])
        assert len(result) == 2
        assert stats.deduped_options == 0


def _opt_with_lines(
    start: int,
    end: int,
    short: list[str] | None = None,
    long: list[str] | None = None,
) -> Option:
    return Option(
        text="desc",
        short=short or [f"-{chr(97 + start % 26)}"],
        long=long or [],
        meta={"lines": [start, end]},
    )


class TestSanityCheckLineSpans:
    """Reject pathological LLM extractions based on line-span coverage."""

    def test_normal_extraction_passes(self) -> None:
        """Typical well-behaved extraction: each option spans a few lines."""
        options = [
            _opt_with_lines(1, 5),
            _opt_with_lines(6, 10),
            _opt_with_lines(11, 100),
        ]
        sanity_check_line_spans(options)

    def test_coverage_too_high(self) -> None:
        """Every option spanning the whole document triggers coverage check."""
        options = [_opt_with_lines(1, 1000, short=[f"-{i}"]) for i in range(20)]
        with pytest.raises(ExtractionError, match="line-span coverage"):
            sanity_check_line_spans(options)

    def test_no_meta_skipped(self) -> None:
        """Options without line metadata are silently ignored."""
        options = [_opt("desc", short=["-a"]), _opt("desc", short=["-b"])]
        sanity_check_line_spans(options)

    def test_empty_options_skipped(self) -> None:
        sanity_check_line_spans([])
