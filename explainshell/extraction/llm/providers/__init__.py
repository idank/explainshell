"""LLM provider protocols and factory."""

from __future__ import annotations

from typing import Any, NamedTuple, Protocol


class TokenUsage:
    """Simple container for token counts."""

    __slots__ = ("input_tokens", "output_tokens", "reasoning_tokens")

    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.reasoning_tokens = reasoning_tokens


class LLMProvider(Protocol):
    """Single-file call interface."""

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]: ...

    def call(self, user_content: str) -> tuple[str, TokenUsage]: ...


class BatchResults(NamedTuple):
    """Return type for BatchProvider.collect_results."""

    responses: dict[str, str]
    """Mapping of request custom_id to LLM response text."""
    usage: TokenUsage


class BatchEntry(NamedTuple):
    """A single entry in a batch."""

    key: str
    """Unique identifier to correlate this request with its response
    (e.g. ``"3:1"`` for work-item 3, chunk 1).  Mapped to the
    provider's ``custom_id`` / ``metadata`` field."""

    user_content: str
    """The user-role prompt text sent to the LLM."""


class BatchProvider(Protocol):
    """Batch API interface (not all providers support this)."""

    def submit_batch(self, entries: list[BatchEntry]) -> str: ...

    def make_poll_client(self) -> Any: ...

    def poll_batch(self, client: Any, job_id: str, poll_interval: int = 30) -> Any: ...

    def collect_results(self, job: Any) -> BatchResults: ...


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
