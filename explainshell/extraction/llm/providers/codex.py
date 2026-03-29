"""Codex CLI provider implementation (no batch support)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from explainshell.errors import FatalExtractionError
from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import TokenUsage

CODEX_TIMEOUT_SECONDS = 600


class CodexProvider:
    """Implements LLMProvider by shelling out to ``codex exec``.

    Uses ``--json`` to get JSONL output on stdout, which includes
    ``item.completed`` events carrying the assistant response and
    ``turn.completed`` events with token usage.
    """

    def __init__(self, model: str, *, codex_bin: str | Sequence[str] = "codex") -> None:
        # model format: "codex/<model>" or "codex/<model>/<reasoning_effort>"
        # e.g. "codex/gpt-5.4-mini" or "codex/gpt-5.4-mini/high"
        parts = model.split("/", 2)
        if len(parts) < 2 or not parts[1]:
            raise ValueError(
                f"codex provider requires a model name (e.g. codex/gpt-5.4-mini), got: {model!r}"
            )
        self._model = model
        self._underlying = parts[1]
        self._reasoning_effort = parts[2] if len(parts) == 3 else None
        self._codex_bin = [codex_bin] if isinstance(codex_bin, str) else list(codex_bin)

    def call(self, user_content: str) -> tuple[str, TokenUsage]:
        prompt = SYSTEM_PROMPT + "\n\n" + user_content

        cmd = [
            *self._codex_bin,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
        ]

        cmd.extend(["--model", self._underlying])

        if self._reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{self._reasoning_effort}"'])

        # Pass prompt on stdin (the "-" argument tells codex to read from stdin).
        cmd.append("-")

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CODEX_TIMEOUT_SECONDS,
        )

        content, usage = _parse_jsonl(result.stdout)

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            if "usage limit" in detail.lower():
                raise FatalExtractionError(f"codex usage limit reached: {detail}")
            # If codex produced no response content, the failure is
            # systemic (auth, network, service outage) rather than
            # prompt-specific — abort the entire run instead of failing
            # each remaining file one by one.
            if not content:
                raise FatalExtractionError(
                    f"codex exec failed with no output (exit {result.returncode}): {detail}"
                )
            raise RuntimeError(
                f"codex exec failed (exit {result.returncode}): {detail}"
            )

        return content, usage

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return (subprocess.TimeoutExpired,)


def _parse_jsonl(jsonl_output: str) -> tuple[str, TokenUsage]:
    """Extract response content and token usage from codex JSONL output.

    Looks for ``item.completed`` events whose ``item.type`` is
    ``"agent_message"`` to capture the assistant text, and
    ``turn.completed`` events for token usage.  Multiple turns are
    summed.
    """
    usage = TokenUsage()
    last_message = ""
    for line in jsonl_output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                last_message = item.get("text", "")
        elif etype == "turn.completed":
            turn_usage = event.get("usage") or {}
            usage.input_tokens += turn_usage.get("input_tokens", 0)
            usage.output_tokens += turn_usage.get("output_tokens", 0)
    return last_message, usage
