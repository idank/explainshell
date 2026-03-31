"""OpenAI provider implementation."""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time

import openai
from openai import OpenAI
from openai.types import Batch

from explainshell.errors import ExtractionError
from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import BatchEntry, BatchResults, TokenUsage

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 300
CANCEL_WAIT_TIMEOUT = 600  # max seconds to wait for cancellation to finalize
STALL_TIMEOUT = 79200  # max seconds (22h) with no progress before cancelling a batch
MAX_POLL_ERRORS = 10  # consecutive poll errors before giving up
MAX_ERROR_BACKOFF = 300  # max seconds between poll error retries


def _openai_input(user_content: str) -> list[dict[str, str]]:
    """Build the Responses API input message list."""
    return [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _azure_base_url() -> str:
    """Return the Azure OpenAI v1 base URL.

    Supports either a fully-qualified base URL or the standard Azure endpoint.
    """
    base_url = os.environ.get("AZURE_OPENAI_BASE_URL")
    if base_url:
        return base_url.rstrip("/") + "/"

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        return endpoint.rstrip("/") + "/openai/v1/"

    raise ValueError(
        "azure/ models require AZURE_OPENAI_BASE_URL or AZURE_OPENAI_ENDPOINT"
    )


class OpenAIProvider:
    """Implements LLMProvider + BatchProvider for OpenAI."""

    def __init__(
        self,
        model: str,
        *,
        reasoning_effort: str | None = None,
        stall_timeout: int = STALL_TIMEOUT,
    ) -> None:
        self._model = model
        if model.startswith("openai/"):
            self._backend = "openai"
            self._api_model = model.removeprefix("openai/")
        elif model.startswith("azure/"):
            self._backend = "azure"
            self._api_model = model.removeprefix("azure/")
        else:
            raise ValueError(f"unsupported OpenAI-compatible model prefix: {model!r}")
        self._reasoning_effort = reasoning_effort
        self._stall_timeout = stall_timeout

    def _make_client(self) -> OpenAI:
        if self._backend == "azure":
            api_key = os.environ.get("AZURE_OPENAI_API_KEY")
            if not api_key:
                raise ValueError("azure/ models require AZURE_OPENAI_API_KEY")
            return OpenAI(
                api_key=api_key,
                base_url=_azure_base_url(),
                timeout=LLM_TIMEOUT_SECONDS,
            )
        return OpenAI(timeout=LLM_TIMEOUT_SECONDS)

    def call(self, user_content: str) -> tuple[str, TokenUsage]:
        client = self._make_client()
        kwargs: dict = {}
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        response = client.responses.create(
            model=self._api_model,
            input=_openai_input(user_content),
            text={"format": {"type": "json_object"}},
            **kwargs,
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
        client = self._make_client()

        buf = io.BytesIO()
        for req in entries:
            body: dict = {
                "model": self._api_model,
                "input": [
                    {"role": "developer", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": req.user_content},
                ],
                "text": {"format": {"type": "json_object"}},
            }
            if self._reasoning_effort:
                body["reasoning"] = {"effort": self._reasoning_effort}
            line = json.dumps(
                {
                    "custom_id": req.key,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": body,
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
        return self._make_client()

    def cancel_batch(self, client: OpenAI, job_id: str) -> None:
        client.batches.cancel(job_id)

    def retrieve_batch(self, batch_id: str) -> Batch:
        """Retrieve a batch by ID (for salvage/inspection)."""
        client = self._make_client()
        return client.batches.retrieve(batch_id)

    def poll_batch(
        self,
        client: OpenAI,
        job_id: str,
        poll_interval: int,
        stop_event: threading.Event | None,
    ) -> Batch:
        consecutive_errors = 0
        prev_counts: tuple[int, int] = (0, 0)
        prev_status: str | None = None
        start_time = time.monotonic()
        cancel_initiated_at: float | None = None
        while True:
            try:
                batch = client.batches.retrieve(job_id)
                consecutive_errors = 0
            except self.retryable_exceptions as e:
                consecutive_errors += 1
                backoff = min(
                    poll_interval * 2 ** (consecutive_errors - 1),
                    MAX_ERROR_BACKOFF,
                )
                logger.warning(
                    "batch %s: poll error (%d/%d), retrying in %ds: %s",
                    job_id,
                    consecutive_errors,
                    MAX_POLL_ERRORS,
                    backoff,
                    e,
                )
                if consecutive_errors >= MAX_POLL_ERRORS:
                    raise ExtractionError(
                        f"Batch poll failed after {MAX_POLL_ERRORS} consecutive errors: {e}"
                    ) from e
                if stop_event is not None:
                    stop_event.wait(backoff)
                    if stop_event.is_set():
                        raise KeyboardInterrupt
                else:
                    time.sleep(backoff)
                continue
            except Exception as e:
                raise ExtractionError(
                    f"Batch poll failed with non-retryable error: {e}"
                ) from e

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
                if cancel_initiated_at is not None:
                    # We cancelled it due to stall — return for partial result collection.
                    return batch
                raise ExtractionError(f"Batch job cancelled: {job_id}")
            if status == "expired":
                logger.warning(
                    "batch %s: expired%s, collecting partial results...",
                    job_id,
                    counts_str,
                )
                return batch

            # Log progress changes at INFO, unchanged polls at DEBUG.
            curr_counts = (counts.completed, counts.failed) if counts else (0, 0)
            if curr_counts != prev_counts or status != prev_status:
                logger.info(
                    "batch %s: status=%s%s",
                    job_id,
                    status,
                    counts_str,
                )
                prev_counts = curr_counts
                prev_status = status
            else:
                logger.debug(
                    "batch %s: status=%s%s, polling again in %ds...",
                    job_id,
                    status,
                    counts_str,
                    poll_interval,
                )

            now = time.monotonic()

            # Wall-time deadline: the API caps batches at 24h, so cancel
            # before that to attempt partial result collection.
            if cancel_initiated_at is None:
                elapsed = now - start_time
                if elapsed >= self._stall_timeout:
                    elapsed_min = int(elapsed // 60)
                    logger.warning(
                        "batch %s: wall-time limit reached (%d minutes)%s, cancelling...",
                        job_id,
                        elapsed_min,
                        counts_str,
                    )
                    try:
                        client.batches.cancel(job_id)
                    except Exception as e:
                        raise ExtractionError(
                            f"Batch wall-time limit reached and cancel failed: {e}"
                        ) from e
                    cancel_initiated_at = now
            else:
                cancel_wait = now - cancel_initiated_at
                if cancel_wait >= CANCEL_WAIT_TIMEOUT:
                    cancel_min = int(cancel_wait // 60)
                    logger.warning(
                        "batch %s: cancellation did not complete after "
                        "%d minutes%s, collecting partial results...",
                        job_id,
                        cancel_min,
                        counts_str,
                    )
                    return batch

            if stop_event is not None:
                stop_event.wait(poll_interval)
                if stop_event.is_set():
                    raise KeyboardInterrupt
            else:
                time.sleep(poll_interval)

    def collect_results(self, job: Batch) -> BatchResults:
        results: dict[str, str] = {}
        usage = TokenUsage()

        if job.usage:
            usage.input_tokens = job.usage.input_tokens
            usage.output_tokens = job.usage.output_tokens

        if not job.output_file_id:
            return BatchResults(results, usage)

        client = self._make_client()
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
