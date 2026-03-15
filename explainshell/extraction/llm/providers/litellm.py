"""LiteLLM provider implementation (fallback for non-Gemini, non-OpenAI models)."""

from __future__ import annotations

import litellm

from explainshell.extraction.llm import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import TokenUsage

LLM_TIMEOUT_SECONDS = 300


class LiteLLMProvider:
    """Implements LLMProvider only (no batch support)."""

    def __init__(self, model: str) -> None:
        self._model = model

    def call(self, user_content: str) -> tuple[str, TokenUsage]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        kwargs: dict = {"response_format": {"type": "json_object"}}
        try:
            info = litellm.get_model_info(self._model)
            max_out = info.get("max_output_tokens")
            if max_out:
                kwargs["max_tokens"] = max_out
        except Exception:
            pass

        response = litellm.completion(
            model=self._model,
            messages=messages,
            timeout=LLM_TIMEOUT_SECONDS,
            num_retries=0,
            **kwargs,
        )
        usage = TokenUsage()
        if response.usage:
            usage.input_tokens = response.usage.prompt_tokens or 0
            usage.output_tokens = response.usage.completion_tokens or 0
        return response.choices[0].message.content, usage

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return (
            litellm.RateLimitError,
            litellm.Timeout,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
            litellm.InternalServerError,
        )
