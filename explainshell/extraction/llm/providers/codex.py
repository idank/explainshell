"""Codex CLI provider implementation (no batch support)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence

from explainshell.extraction.llm.prompt import SYSTEM_PROMPT
from explainshell.extraction.llm.providers import TokenUsage

CODEX_TIMEOUT_SECONDS = 600


class CodexProvider:
    """Implements LLMProvider by shelling out to ``codex exec``.

    Uses ``--json`` to get JSONL output on stdout, which includes a
    ``turn.completed`` event with token usage.  The last assistant
    message is captured via ``-o``.
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

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as response_file:
            response_path = response_file.name

        cmd = [
            *self._codex_bin,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "-o",
            response_path,
        ]

        cmd.extend(["--model", self._underlying])

        if self._reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{self._reasoning_effort}"'])

        # Pass prompt on stdin (the "-" argument tells codex to read from stdin).
        cmd.append("-")

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=CODEX_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"codex exec failed (exit {result.returncode}): {result.stderr.strip()}"
                )

            with open(response_path) as f:
                content = f.read()
        finally:
            os.unlink(response_path)

        usage = _parse_usage(result.stdout)
        return content, usage

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return (subprocess.TimeoutExpired,)


def _parse_usage(jsonl_output: str) -> TokenUsage:
    """Extract token usage from codex JSONL output.

    Looks for ``turn.completed`` events which carry a ``usage`` object
    with ``input_tokens`` and ``output_tokens``.  Multiple turns are
    summed.
    """
    usage = TokenUsage()
    for line in jsonl_output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed":
            continue
        turn_usage = event.get("usage") or {}
        usage.input_tokens += turn_usage.get("input_tokens", 0)
        usage.output_tokens += turn_usage.get("output_tokens", 0)
    return usage
