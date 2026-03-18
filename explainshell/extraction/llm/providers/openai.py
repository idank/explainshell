"""OpenAI provider implementation."""

from __future__ import annotations

import io
import json
import logging
import time

import openai
from openai import OpenAI
from openai.types import Batch

from explainshell.errors import ExtractionError
from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import BatchEntry, BatchResults, TokenUsage

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 300


def _openai_input(user_content: str) -> list[dict[str, str]]:
    """Build the Responses API input message list."""
    return [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


class OpenAIProvider:
    """Implements LLMProvider + BatchProvider for OpenAI."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._openai_model = model.removeprefix("openai/")

    def call(self, user_content: str) -> tuple[str, TokenUsage]:
        client = OpenAI(timeout=LLM_TIMEOUT_SECONDS)
        response = client.responses.create(
            model=self._openai_model,
            input=_openai_input(user_content),
            text={"format": {"type": "json_object"}},
        )
        usage = TokenUsage()
        if response.usage:
            usage.input_tokens = response.usage.input_tokens
            usage.output_tokens = response.usage.output_tokens
            usage.reasoning_tokens = (
                response.usage.output_tokens_details.reasoning_tokens
            )
        return response.output_text, usage

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        )

    # -- Batch API --

    def submit_batch(self, entries: list[BatchEntry]) -> str:
        client = OpenAI(timeout=LLM_TIMEOUT_SECONDS)

        buf = io.BytesIO()
        for req in entries:
            line = json.dumps(
                {
                    "custom_id": req.key,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": {
                        "model": self._openai_model,
                        "input": [
                            {"role": "developer", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": req.user_content},
                        ],
                        "text": {"format": {"type": "json_object"}},
                    },
                }
            )
            buf.write(line.encode("utf-8"))
            buf.write(b"\n")
        buf.seek(0)

        file_obj = client.files.create(file=("batch_input.jsonl", buf), purpose="batch")
        logger.info("uploaded batch input file: %s", file_obj.id)

        batch = client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"source": "explainshell"},
        )
        return batch.id

    def make_poll_client(self) -> OpenAI:
        return OpenAI(timeout=LLM_TIMEOUT_SECONDS)

    def poll_batch(self, client: OpenAI, job_id: str, poll_interval: int = 30) -> Batch:
        consecutive_errors = 0
        max_consecutive_errors = 5
        while True:
            try:
                batch = client.batches.retrieve(job_id)
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

            status = batch.status
            counts = batch.request_counts
            counts_str = ""
            if counts:
                counts_str = f" (completed={counts.completed}, failed={counts.failed}, total={counts.total})"

            if status == "completed":
                return batch
            if status == "failed":
                raise ExtractionError(f"Batch job failed: {job_id}")
            if status == "cancelled":
                raise ExtractionError(f"Batch job cancelled: {job_id}")
            if status == "expired":
                raise ExtractionError(f"Batch job expired: {job_id}")

            logger.info(
                "batch %s: status=%s%s, polling again in %ds...",
                job_id,
                status,
                counts_str,
                poll_interval,
            )
            time.sleep(poll_interval)

    def collect_results(self, job: Batch) -> BatchResults:
        results: dict[str, str] = {}
        usage = TokenUsage()

        if job.usage:
            usage.input_tokens = job.usage.input_tokens
            usage.output_tokens = job.usage.output_tokens

        if not job.output_file_id:
            return BatchResults(results, usage)

        client = OpenAI(timeout=LLM_TIMEOUT_SECONDS)
        content = client.files.content(job.output_file_id)

        per_request_input = 0
        per_request_output = 0
        per_request_reasoning = 0

        for line in content.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("custom_id", "")
            response = row.get("response", {})
            body = response.get("body", {})

            req_usage = body.get("usage", {})
            per_request_input += req_usage.get("input_tokens", 0)
            per_request_output += req_usage.get("output_tokens", 0)
            od = req_usage.get("output_tokens_details", {}) or {}
            per_request_reasoning += od.get("reasoning_tokens", 0)

            text = None
            for item in body.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            text = part.get("text", "")
                            break
                    if text is not None:
                        break
            if text is not None:
                results[key] = text
            else:
                error = row.get("error")
                logger.warning(
                    "batch response for key %s has no content (error=%s)", key, error
                )

        if not job.usage:
            usage.input_tokens = per_request_input
            usage.output_tokens = per_request_output
        usage.reasoning_tokens = per_request_reasoning

        if job.error_file_id:
            try:
                error_content = client.files.content(job.error_file_id)
                for line in error_content.text.splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    key = row.get("custom_id", "unknown")
                    error = row.get("error", {})
                    logger.warning("batch request %s error: %s", key, error)
            except Exception as e:
                logger.warning("failed to download batch error file: %s", e)

        return BatchResults(results, usage)
