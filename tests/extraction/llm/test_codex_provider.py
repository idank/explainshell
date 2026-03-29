"""Tests for the Codex CLI provider."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from explainshell.errors import FatalExtractionError
from explainshell.extraction.llm.providers import make_provider
from explainshell.extraction.llm.providers.codex import CodexProvider, _parse_jsonl


def _fake_codex(script: str) -> list[str]:
    """Return a codex_bin command list that runs *script* via bash -c.

    The ``--`` serves as argv0 so the provider's arguments (``exec --json …``)
    land in ``$1 $2 …`` as the test scripts expect.
    """
    return ["bash", "-c", script, "--"]


def _jsonl_response(text: str, input_tokens: int = 0, output_tokens: int = 0) -> str:
    """Return a bash snippet that emits JSONL events mimicking codex output."""
    item_event = json.dumps(
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": text},
        }
    )
    usage_event = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )
    return f"echo '{item_event}'\necho '{usage_event}'\n"


class TestParseJsonl(unittest.TestCase):
    """Tests for _parse_jsonl JSONL parsing."""

    def test_single_turn(self) -> None:
        jsonl = (
            '{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":50}}\n'
        )
        content, usage = _parse_jsonl(jsonl)
        assert content == "hi"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 50

    def test_multiple_turns(self) -> None:
        jsonl = (
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":200,"output_tokens":30}}\n'
        )
        content, usage = _parse_jsonl(jsonl)
        assert content == ""
        assert usage.input_tokens == 300
        assert usage.output_tokens == 50

    def test_empty_output(self) -> None:
        content, usage = _parse_jsonl("")
        assert content == ""
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_no_turn_completed(self) -> None:
        jsonl = '{"type":"thread.started","thread_id":"abc"}\n{"type":"turn.started"}\n'
        content, usage = _parse_jsonl(jsonl)
        assert content == ""
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_malformed_json_lines_skipped(self) -> None:
        jsonl = (
            "not json at all\n"
            '{"type":"turn.completed","usage":{"input_tokens":500,"output_tokens":60}}\n'
            "{broken\n"
        )
        content, usage = _parse_jsonl(jsonl)
        assert content == ""
        assert usage.input_tokens == 500
        assert usage.output_tokens == 60

    def test_blank_lines_skipped(self) -> None:
        jsonl = (
            "\n"
            "  \n"
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
            "\n"
        )
        content, usage = _parse_jsonl(jsonl)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5

    def test_missing_usage_field(self) -> None:
        jsonl = '{"type":"turn.completed"}\n'
        _, usage = _parse_jsonl(jsonl)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_null_usage_field(self) -> None:
        jsonl = '{"type":"turn.completed","usage":null}\n'
        _, usage = _parse_jsonl(jsonl)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_last_agent_message_wins(self) -> None:
        jsonl = (
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"first"}}\n'
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"second"}}\n'
        )
        content, _ = _parse_jsonl(jsonl)
        assert content == "second"

    def test_non_agent_message_ignored(self) -> None:
        jsonl = '{"type":"item.completed","item":{"id":"item_0","type":"tool_call","text":"nope"}}\n'
        content, _ = _parse_jsonl(jsonl)
        assert content == ""


class TestCodexProviderCall(unittest.TestCase):
    """Tests for CodexProvider.call() using inline bash commands."""

    def test_returns_response_and_usage(self) -> None:
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(_jsonl_response('{"options": []}', 100, 20)),
        )
        content, usage = provider.call("extract options")

        assert content == '{"options": []}'
        assert usage.input_tokens == 100
        assert usage.output_tokens == 20

    def test_bare_codex_rejected(self) -> None:
        """Bare 'codex' without a model name should raise ValueError."""
        with self.assertRaises(ValueError):
            CodexProvider("codex")

    def test_codex_with_model_passes_flag(self) -> None:
        """'codex/o3' should pass --model o3."""
        provider = CodexProvider(
            "codex/o3",
            codex_bin=_fake_codex(
                # Fail unless --model o3 is present.
                "found=\n"
                'shift  # skip "exec"\n'
                "while [ $# -gt 0 ]; do\n"
                '    case "$1" in --model) shift; [ "$1" = "o3" ] && found=1; shift ;; *) shift ;; esac\n'
                "done\n"
                '[ -n "$found" ] || { echo "expected --model o3" >&2; exit 1; }\n'
                + _jsonl_response("{}")
            ),
        )
        provider.call("test")

    def test_reasoning_effort_passed(self) -> None:
        """'codex/gpt-5.4-mini/high' should pass -c model_reasoning_effort."""
        provider = CodexProvider(
            "codex/gpt-5.4-mini/high",
            codex_bin=_fake_codex(
                "found=\n"
                'shift  # skip "exec"\n'
                "while [ $# -gt 0 ]; do\n"
                '    case "$1" in\n'
                '        -c) shift; case "$1" in *model_reasoning_effort*high*) found=1 ;; esac; shift ;;\n'
                "        *) shift ;;\n"
                "    esac\n"
                "done\n"
                '[ -n "$found" ] || { echo "expected reasoning effort" >&2; exit 1; }\n'
                + _jsonl_response("{}")
            ),
        )
        provider.call("test")

    def test_no_reasoning_effort_without_third_segment(self) -> None:
        """'codex/o3' should not pass -c model_reasoning_effort."""
        provider = CodexProvider(
            "codex/o3",
            codex_bin=_fake_codex(
                'for arg in "$@"; do\n'
                '    [ "$arg" = "-c" ] && { echo "unexpected -c flag" >&2; exit 1; }\n'
                "done\n" + _jsonl_response("{}")
            ),
        )
        provider.call("test")

    def test_nonzero_exit_no_output_raises_fatal(self) -> None:
        """Exit 1 with no JSONL response content → FatalExtractionError."""
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex('echo -n "something went wrong" >&2; exit 1'),
        )
        with self.assertRaises(FatalExtractionError) as ctx:
            provider.call("test")

        assert "no output" in str(ctx.exception)
        assert "something went wrong" in str(ctx.exception)

    def test_nonzero_exit_with_output_raises_runtime(self) -> None:
        """Exit 1 but JSONL has response content → RuntimeError."""
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(_jsonl_response("partial") + "exit 1\n"),
        )
        with self.assertRaises(RuntimeError) as ctx:
            provider.call("test")

        assert "exit 1" in str(ctx.exception)

    def test_prompt_includes_system_prompt(self) -> None:
        import tempfile

        from explainshell.extraction.llm.prompt import SYSTEM_PROMPT

        # Capture stdin to a temp file so we can inspect it.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            capture_path = f.name

        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(f'cat > "{capture_path}"\n' + _jsonl_response("{}")),
        )
        try:
            provider.call("my user content")
            with open(capture_path) as f:
                captured = f.read()
        finally:
            os.unlink(capture_path)

        assert captured.startswith(SYSTEM_PROMPT)
        assert "my user content" in captured


class TestRetryableExceptions(unittest.TestCase):
    def test_timeout_is_retryable(self) -> None:
        provider = CodexProvider("codex/test")
        assert subprocess.TimeoutExpired in provider.retryable_exceptions


class TestMakeProviderRouting(unittest.TestCase):
    """make_provider routes codex models to CodexProvider."""

    def test_bare_codex_raises(self) -> None:
        """Bare 'codex' routes to CodexProvider which rejects it."""
        with self.assertRaises(ValueError):
            make_provider("codex")

    def test_codex_with_model(self) -> None:
        provider = make_provider("codex/o3")
        assert isinstance(provider, CodexProvider)

    def test_codex_with_reasoning_effort(self) -> None:
        provider = make_provider("codex/gpt-5.4-mini/high")
        assert isinstance(provider, CodexProvider)


@unittest.skipUnless(
    os.environ.get("RUN_LLM_TESTS") == "1", "set RUN_LLM_TESTS=1 to run"
)
class TestRealCodex(unittest.TestCase):
    """Integration test that calls the real codex binary."""

    def test_simple_prompt(self) -> None:
        provider = CodexProvider("codex/gpt-5.4")
        content, usage = provider.call(
            'Return exactly this JSON and nothing else: {"options": []}'
        )
        data = json.loads(content)
        assert "options" in data
        assert usage.input_tokens > 0


if __name__ == "__main__":
    unittest.main()
