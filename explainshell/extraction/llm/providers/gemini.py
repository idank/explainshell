"""Gemini provider implementation."""

from __future__ import annotations

import logging
import os
import time

import httpx
from google import genai
from google.genai import Client, types
from google.genai.errors import ClientError, ServerError
from google.genai.types import BatchJob

from explainshell.errors import ExtractionError
from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import BatchEntry, BatchResults, TokenUsage

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 300


class GeminiProvider:
    """Implements LLMProvider + BatchProvider for Gemini."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._gemini_model = model.removeprefix("gemini/")

    def call(self, user_content: str) -> tuple[str, TokenUsage]:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=self._gemini_model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                http_options=types.HttpOptions(timeout=LLM_TIMEOUT_SECONDS * 1000),
            ),
        )
        usage = TokenUsage()
        um = response.usage_metadata
        if um:
            usage.input_tokens = um.prompt_token_count or 0
            usage.output_tokens = um.candidates_token_count or 0
            usage.reasoning_tokens = um.thoughts_token_count or 0
        return response.text, usage

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return (ClientError, ServerError, httpx.TimeoutException)

    # -- Batch API --

    def submit_batch(self, entries: list[BatchEntry]) -> str:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        inline_entries = []
        for entry in entries:
            inline_entries.append(
                types.InlinedRequest(
                    contents=entry.user_content,
                    metadata={"key": entry.key},
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                    ),
                )
            )

        job = client.batches.create(
            model=self._gemini_model,
            src=inline_entries,
            config=types.CreateBatchJobConfig(display_name="explainshell-batch"),
        )
        return job.name

    def make_poll_client(self) -> Client:
        return genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    def poll_batch(
        self, client: Client, job_id: str, poll_interval: int = 30
    ) -> BatchJob:
        consecutive_errors = 0
        max_consecutive_errors = 5
        while True:
            try:
                job = client.batches.get(name=job_id)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.warning(
                    "batch %s: poll error (%d/%d): %s",
                    job_id,
                    consecutive_errors,
                    max_consecutive_errors,
                    e,
                )
                if consecutive_errors >= max_consecutive_errors:
                    raise ExtractionError(
                        f"Batch poll failed after {max_consecutive_errors} consecutive errors: {e}"
                    ) from e
                time.sleep(poll_interval)
                continue

            state = job.state.name if hasattr(job.state, "name") else str(job.state)

            if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED"):
                return job
            if state in ("JOB_STATE_FAILED", "FAILED"):
                raise ExtractionError(f"Batch job failed: {job_id}")
            if state in ("JOB_STATE_CANCELLED", "CANCELLED"):
                raise ExtractionError(f"Batch job cancelled: {job_id}")
            if state in ("JOB_STATE_EXPIRED", "EXPIRED"):
                raise ExtractionError(f"Batch job expired: {job_id}")

            logger.info(
                "batch %s: state=%s, polling again in %ds...",
                job_id,
                state,
                poll_interval,
            )
            time.sleep(poll_interval)

    def collect_results(self, job: BatchJob) -> BatchResults:
        results: dict[str, str] = {}
        usage = TokenUsage()
        if not job.dest or not job.dest.inlined_responses:
            return BatchResults(results, usage)
        for resp in job.dest.inlined_responses:
            key = (resp.metadata or {}).get("key", "")
            if resp.response and resp.response.candidates:
                text = resp.response.candidates[0].content.parts[0].text
                results[key] = text
                um = resp.response.usage_metadata
                if um:
                    usage.input_tokens += um.prompt_token_count or 0
                    usage.output_tokens += um.candidates_token_count or 0
                    usage.reasoning_tokens += um.thoughts_token_count or 0
            else:
                logger.warning("batch response for key %s has no content", key)
        return BatchResults(results, usage)
