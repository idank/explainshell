"""LLM provider protocols and factory."""

from __future__ import annotations

from typing import Any, Protocol


class TokenUsage:
    """Simple container for token counts."""

    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LLMProvider(Protocol):
    """Single-file call interface."""

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]: ...

    def call(self, user_content: str) -> tuple[str, TokenUsage]: ...


class BatchProvider(Protocol):
    """Batch API interface (not all providers support this)."""

    def submit_batch(self, requests: list[tuple[str, str]]) -> Any: ...

    def make_poll_client(self) -> Any: ...

    def poll_batch(self, client: Any, job_id: str, poll_interval: int = 30) -> Any: ...

    def collect_results(self, job: Any) -> tuple[dict[str, str], TokenUsage]: ...


def make_provider(model: str) -> LLMProvider:
    """Create a provider for the given model string."""
    if model.startswith("gemini/"):
        from explainshell.extraction.llm.providers.gemini import GeminiProvider

        return GeminiProvider(model)
    if model.startswith("openai/"):
        from explainshell.extraction.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(model)

    from explainshell.extraction.llm.providers.litellm import LiteLLMProvider

    return LiteLLMProvider(model)


def make_batch_provider(model: str) -> BatchProvider:
    """Create a batch-capable provider for the given model string."""
    if model.startswith("gemini/"):
        from explainshell.extraction.llm.providers.gemini import GeminiProvider

        return GeminiProvider(model)
    if model.startswith("openai/"):
        from explainshell.extraction.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(model)
    raise ValueError(f"Batch mode is not supported for model: {model}")
