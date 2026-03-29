"""LLM extractor orchestration and design notes.

This module is the coordinator for the LLM extraction path. It does not own
provider-specific API details, JSON parsing, or text chunking logic; instead it
glues together the other LLM submodules into one extraction pipeline:

1. ``prepare()`` reads the man page, strips mandoc artifacts, removes known
   low-value sections, numbers the remaining lines, and chunks the text.
2. ``extract()`` sends one request per chunk through the configured provider
   and accumulates token/latency stats.
3. ``finalize()`` parses each raw JSON response, converts line references back
   into option text, dedups cross-chunk overlap, runs extractor-agnostic
   postprocessing, and builds ``ParsedManpage`` / ``RawManpage`` results.

Some important design choices are easy to miss when looking only at the public
methods:

- The LLM does *not* return option descriptions directly. It returns line
  ranges into the numbered source text, and ``response.py`` reconstructs the
  final help text locally. This keeps the model output smaller and makes the
  final stored text deterministic.
- Chunking happens on the filtered plain text, but line numbers are relative to
  the original filtered document. That lets multiple chunk responses be merged
  later without renumbering.
- Dedup is intentionally two-layered: ``dedup_ref_options()`` removes obvious
  chunk-overlap duplicates while the data is still in raw dict form, then
  ``postprocess()`` handles higher-level cleanup on validated ``Option``
  objects.
- ``PreparedFile`` is the contract between the normal per-file path and the
  batch runner. Batch mode reuses ``prepare()`` and ``finalize()`` so the
  interactive and batch paths stay behaviorally aligned.

A few experiments are worth recording here because they affect future changes:

- OpenAI Structured Outputs and "minified JSON" were both tried as output-token
  optimizations. They did not reliably reduce billed output enough to justify
  the added complexity, so the OpenAI path still uses ``json_object`` mode.
- We benchmarked lowering OpenAI reasoning effort, but it performed significantly
  worse than the default.

Public API:
    LLMExtractor(config).extract(gz_path) -> ExtractionResult
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import dotenv
from pydantic import ValidationError

from explainshell import manpage
from explainshell.errors import ExtractionError, SkippedExtraction
from explainshell.extraction.common import build_manpage_metadata, build_raw_manpage
from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import (
    BatchProvider,
    LLMProvider,
    TokenUsage,
    make_batch_provider,
    make_provider,
)
from explainshell.extraction.llm.response import (
    normalize_option_fields,
    normalize_subcommands,
    dedup_ref_options,
    llm_option_to_store_option,
    process_llm_result,
)
from explainshell.extraction.llm.text import (
    MAX_MANPAGE_CHARS,
    chunk_text,
    clean_mandoc_artifacts,
    filter_sections,
    get_manpage_text,
    number_lines,
)
from explainshell.extraction.postprocess import postprocess
from explainshell.extraction.types import (
    ExtractionResult,
    ExtractionStats,
    ExtractorConfig,
    Extractor,
)

dotenv.load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class PreparedFile:
    """Result of LLMExtractor.prepare() — everything needed for extraction.

    Fields
    ------
    synopsis:       Synopsis line from lexgrog, or None.
    aliases:        (name, score) tuples for alternative command names.
    original_lines: 1-indexed line number → original line content (before
                    numbering). Used by finalize to map LLM line references
                    back to source text.
    basename:       Manpage file stem without .gz/.section suffixes (e.g. "tar").
    numbered_text:  Full manpage text with "  42| …" line-number prefixes,
                    used for debug dumps.
    plain_text_len: Length of the original plain text before filtering/chunking.
    plain_text:     Original unfiltered manpage text (used for RawManpage storage).
    requests:       Pre-formatted user-content strings, one per chunk, ready to
                    submit to the LLM provider.
    n_chunks:       Derived property — ``len(requests)``.
    """

    synopsis: str | None
    aliases: list[tuple[str, int]]
    original_lines: dict[int, str]
    basename: str
    numbered_text: str
    plain_text_len: int
    plain_text: str
    requests: list[str]

    @property
    def n_chunks(self) -> int:
        return len(self.requests)


@runtime_checkable
class BatchExtractor(Extractor, Protocol):
    """Extractor that supports batch execution via a provider API."""

    @property
    def batch_provider(self) -> BatchProvider: ...

    def prepare(self, gz_path: str) -> PreparedFile: ...

    def finalize(
        self, gz_path: str, prepared: PreparedFile, responses: list[str]
    ) -> ExtractionResult: ...


@dataclass
class ChunkResult:
    """Result of a single LLM call for one chunk."""

    data: dict
    messages: list[dict[str, str]]
    raw_response: str
    usage: TokenUsage


class LLMExtractor:
    """LLM-based option extractor.

    Implements the base ``Extractor`` protocol via ``extract()``.
    Also satisfies ``BatchExtractor`` via ``prepare()``, ``finalize()``,
    and ``batch_provider``.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        self._model = config.model or ""
        self._debug_dir = config.debug_dir
        self._fail_dir = config.fail_dir
        self._provider_instance: LLMProvider | None = None
        self._batch_provider_instance: BatchProvider | None = None

    @property
    def provider(self) -> LLMProvider:
        if self._provider_instance is None:
            self._provider_instance = make_provider(self._model)
        return self._provider_instance

    @property
    def batch_provider(self) -> BatchProvider:
        if self._batch_provider_instance is None:
            self._batch_provider_instance = make_batch_provider(self._model)
        return self._batch_provider_instance

    def extract(self, gz_path: str) -> ExtractionResult:
        """Full extraction pipeline: prepare → LLM calls → finalize."""
        prepared = self.prepare(gz_path)
        basename = prepared.basename
        n_chunks = prepared.n_chunks

        logger.info(
            "%s: %d chars (%d numbered), %d chunk(s)",
            basename,
            prepared.plain_text_len,
            len(prepared.numbered_text),
            n_chunks,
        )

        stats = ExtractionStats(
            chunks=n_chunks,
            plain_text_len=prepared.plain_text_len,
        )

        all_chunk_data: list[ChunkResult] = []
        t0 = time.monotonic()

        for i, user_content in enumerate(prepared.requests):
            chunk_label = (
                f"chunk {i + 1}/{n_chunks}" if n_chunks > 1 else "single chunk"
            )
            logger.info(
                "%s: calling LLM (%s, %d chars)...",
                basename,
                chunk_label,
                len(user_content),
            )

            try:
                cr = self._call_llm(user_content)
            except ExtractionError as e:
                if e.raw_response:
                    self._dump_failed_response(basename, i, e.raw_response)
                raise

            stats.input_tokens += cr.usage.input_tokens
            stats.output_tokens += cr.usage.output_tokens
            stats.reasoning_tokens += cr.usage.reasoning_tokens
            n_opts = len(cr.data["options"])
            logger.info(
                "%s: LLM returned %d option(s) for %s",
                basename,
                n_opts,
                chunk_label,
            )
            all_chunk_data.append(cr)

        stats.elapsed_seconds = time.monotonic() - t0
        return self._finalize(gz_path, prepared, all_chunk_data, stats)

    def prepare(self, gz_path: str) -> PreparedFile:
        """Pre-process a manpage without calling LLM.

        Raises SkippedExtraction if the manpage is too large.
        """
        synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)
        plain_text = clean_mandoc_artifacts(get_manpage_text(gz_path))
        basename = os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]

        if len(plain_text) > MAX_MANPAGE_CHARS:
            raise SkippedExtraction(
                f"manpage too large ({len(plain_text):,} chars, limit {MAX_MANPAGE_CHARS:,})",
                stats=ExtractionStats(plain_text_len=len(plain_text)),
            )

        filtered_text, removal_counts = filter_sections(plain_text)
        if removal_counts:
            logger.debug(
                "%s: filtered sections: %s (saved %d chars)",
                basename,
                ", ".join(f"{k} ({v})" for k, v in sorted(removal_counts.items())),
                len(plain_text) - len(filtered_text),
            )

        numbered_text, original_lines = number_lines(filtered_text)
        chunks = chunk_text(filtered_text)
        n_chunks = len(chunks)

        requests: list[str] = []
        for i, chunk in enumerate(chunks):
            chunk_info = f" (part {i + 1} of {n_chunks})" if n_chunks > 1 else ""
            requests.append(self._build_user_content(chunk, chunk_info))

        return PreparedFile(
            synopsis=synopsis,
            aliases=aliases,
            original_lines=original_lines,
            basename=basename,
            numbered_text=numbered_text,
            plain_text_len=len(plain_text),
            plain_text=plain_text,
            requests=requests,
        )

    def finalize(
        self,
        gz_path: str,
        prepared: PreparedFile,
        responses: list[str],
    ) -> ExtractionResult:
        """Finalize extraction from batch responses.

        Used by run_batch after collecting provider results.  The returned
        stats carry ``chunks`` and ``plain_text_len``; token counts are
        tracked at the batch level by the runner.
        """
        all_chunk_data: list[ChunkResult] = []
        stats = ExtractionStats(
            chunks=prepared.n_chunks,
            plain_text_len=prepared.plain_text_len,
        )

        for chunk_idx, response_text in enumerate(responses):
            try:
                chunk_data, raw = process_llm_result(response_text)
            except ExtractionError as e:
                if e.raw_response:
                    self._dump_failed_response(
                        prepared.basename, chunk_idx, e.raw_response
                    )
                raise

            messages = self._build_messages(prepared.requests[chunk_idx])
            all_chunk_data.append(
                ChunkResult(
                    data=chunk_data,
                    messages=messages,
                    raw_response=raw,
                    usage=TokenUsage(0, 0),
                )
            )

        return self._finalize(gz_path, prepared, all_chunk_data, stats)

    def _finalize(
        self,
        gz_path: str,
        prepared: PreparedFile,
        all_chunk_data: list[ChunkResult],
        stats: ExtractionStats,
    ) -> ExtractionResult:
        """Assemble ExtractionResult from prepared data + chunk results."""
        basename = prepared.basename
        original_lines = prepared.original_lines
        n_chunks = prepared.n_chunks
        numbered_text = prepared.numbered_text

        if self._debug_dir:
            os.makedirs(self._debug_dir, exist_ok=True)
            with open(os.path.join(self._debug_dir, f"{basename}.md"), "w") as f:
                f.write(numbered_text)

        all_raw: list[dict] = []
        dashless_opts = False
        all_subcommands: list[str] = []
        for i, cr in enumerate(all_chunk_data):
            all_raw.extend(cr.data["options"])
            if cr.data.get("dashless_opts"):
                dashless_opts = True
            all_subcommands.extend(cr.data.get("subcommands") or [])

            if self._debug_dir:
                if n_chunks == 1:
                    prompt_name = f"{basename}.prompt.json"
                    response_name = f"{basename}.response.txt"
                else:
                    prompt_name = f"{basename}.chunk-{i}.prompt.json"
                    response_name = f"{basename}.chunk-{i}.response.txt"
                with open(os.path.join(self._debug_dir, prompt_name), "w") as f:
                    json.dump(cr.messages, f, indent=2)
                with open(os.path.join(self._debug_dir, response_name), "w") as f:
                    f.write(cr.raw_response)

        all_raw = dedup_ref_options(all_raw)

        options = []
        for idx, raw_opt in enumerate(all_raw):
            try:
                if normalize_option_fields(raw_opt) != raw_opt:
                    stats.normalized_options += 1
                options.append(llm_option_to_store_option(raw_opt, original_lines))
            except (ValueError, ValidationError) as e:
                logger.warning(
                    "%s: skipping malformed option %d: %s\n  raw: %s",
                    basename,
                    idx,
                    e,
                    json.dumps(raw_opt, default=str)[:200],
                )
                stats.malformed_options += 1

        options, pp_stats = postprocess(options)
        stats.deduped_options += pp_stats.deduped_options
        stats.dropped_empty += pp_stats.dropped_empty

        subcommands = normalize_subcommands(basename, all_subcommands)

        logger.debug("%s: extracted %d option(s) total", basename, len(options))

        mp = build_manpage_metadata(
            gz_path,
            options,
            dashless_opts=dashless_opts,
            subcommands=subcommands,
            extractor="llm",
            extraction_meta={"model": self._model},
        )

        raw_mp = build_raw_manpage(prepared.plain_text, "mandoc -T markdown", gz_path)

        return ExtractionResult(gz_path=gz_path, mp=mp, raw=raw_mp, stats=stats)

    def _call_llm(self, user_content: str) -> ChunkResult:
        """Call LLM via the provider with retries."""
        messages = self._build_messages(user_content)

        provider = self.provider
        retryable = provider.retryable_exceptions

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                content, usage = provider.call(user_content)
                data, raw = process_llm_result(content)
                return ChunkResult(
                    data=data,
                    messages=messages,
                    raw_response=raw,
                    usage=usage,
                )
            except ExtractionError:
                raise
            except retryable as e:
                last_err = e
                wait = 2**attempt
                logger.warning(
                    "LLM call attempt %d failed (%s), retrying in %ds",
                    attempt + 1,
                    e,
                    wait,
                )
                time.sleep(wait)
            except Exception as e:
                raise ExtractionError(f"LLM call failed: {e}") from e

        raise ExtractionError(
            f"LLM call failed after 3 attempts: {last_err}"
        ) from last_err

    @staticmethod
    def _build_user_content(chunk: str, chunk_info: str) -> str:
        """Build the user prompt string for a single chunk."""
        if chunk_info:
            return (
                f"Man page{chunk_info}.\n\n"
                "Extract ALL options documented in THIS part only. "
                "Each part is processed independently — do not wait for "
                "other parts. If this part contains no options, return "
                '{"options": []}.\n\n'
                f"{chunk}"
            )
        return chunk

    @staticmethod
    def _build_messages(user_content: str) -> list[dict[str, str]]:
        """Build messages list for debug output."""
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _dump_failed_response(
        self, basename: str, chunk_idx: int, raw_response: str
    ) -> None:
        """Write a failed LLM response to fail_dir for inspection."""
        if not self._fail_dir:
            return
        os.makedirs(self._fail_dir, exist_ok=True)
        name = f"{basename}.chunk-{chunk_idx}.failed-response.txt"
        path = os.path.join(self._fail_dir, name)
        with open(path, "w") as f:
            f.write(raw_response)
        logger.error("raw LLM response saved to %s", path)
