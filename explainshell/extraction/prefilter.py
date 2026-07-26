"""Pre-extraction classification of input .gz files.

Each file resolves to exactly one ``Decision`` (Work, SizeSkip, AlreadyStored,
FilterSkip, Symlink, ContentDup). ``Classifier.classify`` is a stateful,
single-pass call: it has no external side effects (no DB writes, no logging),
but it does mutate ``_hash_to_canonical`` in the ``Work`` branch so a later
same-hash sibling in the same input set classifies as ``ContentDup``. It is
not safe to call concurrently or to retry. ``apply_decisions`` is the
side-effecting pass: it performs DB cleanup for stale symlinks, emits
per-file log lines, and produces the buckets the extract command consumes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from explainshell import config, models, store
from explainshell.extraction import common

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Work:
    gz_path: str
    short_path: str


@dataclass(frozen=True)
class SizeSkip:
    gz_path: str
    short_path: str
    size: int
    threshold: int
    direction: str  # "small" or "large" — the bucket the filter wanted to keep


@dataclass(frozen=True)
class AlreadyStored:
    gz_path: str
    short_path: str


@dataclass(frozen=True)
class FilterSkip:
    gz_path: str
    short_path: str
    stored_extractor: str
    stored_model: str | None


@dataclass(frozen=True)
class Symlink:
    gz_path: str
    short_path: str
    canonical_source: str
    stale_in_db: bool
    canonical_in_inputs: bool


@dataclass(frozen=True)
class ContentDup:
    gz_path: str
    short_path: str
    canonical_source: str


Decision = Work | SizeSkip | AlreadyStored | FilterSkip | Symlink | ContentDup


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _dedup_key(sha: str, source: str) -> str:
    # Scope by distro/release/section so cross-release lookups still work.
    prefix = source.rsplit("/", 1)[0]
    return f"{sha}:{prefix}"


def _matches_filter(
    filter_specs: list[tuple[str, str | None]],
    stored_extractor: str,
    stored_meta: models.ExtractionMeta,
) -> bool:
    """True if the stored row matches any of the (mode, model) filter specs."""
    if not stored_extractor:
        return False
    for filter_mode, filter_model in filter_specs:
        if filter_mode != stored_extractor:
            continue
        if filter_mode == "llm":
            if stored_meta.model == filter_model:
                return True
            continue
        return True
    return False


@dataclass
class Classifier:
    s: store.Store
    overwrite: bool
    filter_specs: list[tuple[str, str | None]]
    small_only: bool
    large_only: bool
    size_threshold: int
    normalized_inputs: set[str]

    _hash_to_canonical: dict[str, str] = field(init=False, default_factory=dict)
    _filter_index: dict[str, tuple[str, models.ExtractionMeta]] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        # When --overwrite is set we want every canonical to be re-extracted,
        # so we don't seed the dedup map from the DB (in-run dedup still applies).
        if not self.overwrite:
            for sha, source in self.s.known_sha256s().items():
                self._hash_to_canonical[_dedup_key(sha, source)] = source
        if self.filter_specs:
            self._filter_index = self.s.extractor_info_index()

    def classify(self, gz_path: str) -> Decision:
        short_path = config.source_from_path(gz_path)

        if self.small_only or self.large_only:
            size = os.path.getsize(gz_path)
            if self.small_only and size > self.size_threshold:
                return SizeSkip(gz_path, short_path, size, self.size_threshold, "small")
            if self.large_only and size <= self.size_threshold:
                return SizeSkip(gz_path, short_path, size, self.size_threshold, "large")

        if os.path.islink(gz_path):
            canonical_path = os.path.realpath(gz_path)
            canonical_source = config.source_from_path(canonical_path)
            if canonical_source != short_path:
                return Symlink(
                    gz_path=gz_path,
                    short_path=short_path,
                    canonical_source=canonical_source,
                    stale_in_db=self.s.has_manpage_source(short_path),
                    canonical_in_inputs=canonical_path in self.normalized_inputs,
                )

        if self.overwrite and self.filter_specs:
            existing = self._filter_index.get(short_path)
            if existing is not None:
                stored_extractor, stored_meta = existing
                if _matches_filter(
                    self.filter_specs,
                    stored_extractor,
                    stored_meta,
                ):
                    # Matching row: queue for re-extraction. Deliberately skip
                    # the dedup branch — and don't seed _hash_to_canonical —
                    # so a same-hash sibling doesn't silently alias onto this
                    # row's stale parsed_manpages.
                    return Work(gz_path, short_path)
                # Non-matching row: keep its data; don't seed dedup either,
                # for the same reason.
                return FilterSkip(
                    gz_path=gz_path,
                    short_path=short_path,
                    stored_extractor=stored_extractor,
                    stored_model=stored_meta.model,
                )

        if not self.overwrite and self.s.has_manpage_source(short_path):
            return AlreadyStored(gz_path, short_path)

        h = common.gz_sha256(gz_path)
        key = _dedup_key(h, short_path)
        canonical = self._hash_to_canonical.get(key)
        if canonical is not None:
            return ContentDup(gz_path, short_path, canonical)
        self._hash_to_canonical[key] = short_path
        return Work(gz_path, short_path)


# ---------------------------------------------------------------------------
# Decision application
# ---------------------------------------------------------------------------


@dataclass
class Classified:
    work_files: list[str] = field(default_factory=list)
    symlinks: list[tuple[str, str, str]] = field(default_factory=list)
    content_dups: list[tuple[str, str, str]] = field(default_factory=list)
    prefilter_skipped: int = 0
    size_filtered: int = 0
    already_stored: int = 0


def apply_decisions(
    decisions: list[Decision],
    s: store.Store,
    *,
    filter_db: tuple[str, ...] = (),
) -> Classified:
    """Apply DB cleanup and per-file logging; bucket decisions for the caller."""
    out = Classified()
    for d in decisions:
        if isinstance(d, Work):
            out.work_files.append(d.gz_path)
        elif isinstance(d, SizeSkip):
            cmp = ">" if d.direction == "max" else "<="
            logger.debug(
                "size-filter skip %s (%d %s %d)",
                d.short_path,
                d.size,
                cmp,
                d.threshold,
            )
            out.size_filtered += 1
            out.prefilter_skipped += 1
        elif isinstance(d, AlreadyStored):
            logger.debug("skipping %s (already stored)", d.short_path)
            out.already_stored += 1
            out.prefilter_skipped += 1
        elif isinstance(d, FilterSkip):
            logger.debug(
                "filter-skip %s (stored=%s/%s, filter=%s)",
                d.short_path,
                d.stored_extractor,
                d.stored_model,
                ", ".join(filter_db),
            )
            out.prefilter_skipped += 1
        elif isinstance(d, Symlink):
            if d.stale_in_db:
                # CASCADE removes orphan mappings.
                s.delete_manpage(d.short_path)
                logger.info(
                    "removed stale manpage %s (now a symlink to %s)",
                    d.short_path,
                    d.canonical_source,
                )
            if d.canonical_in_inputs:
                logger.info(
                    "skipping symlink %s -> %s (canonical file is in the input set)",
                    d.short_path,
                    d.canonical_source,
                )
            else:
                logger.info(
                    "skipping symlink %s -> %s (pass the canonical file to extract it)",
                    d.short_path,
                    d.canonical_source,
                )
            out.symlinks.append((d.gz_path, d.short_path, d.canonical_source))
        elif isinstance(d, ContentDup):
            out.content_dups.append((d.gz_path, d.short_path, d.canonical_source))
    return out
