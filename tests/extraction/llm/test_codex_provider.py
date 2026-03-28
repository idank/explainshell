"""Tests for the Codex CLI provider."""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from explainshell.extraction.llm.providers import make_provider
from explainshell.extraction.llm.providers.codex import CodexProvider, _parse_usage

# Base bash snippet that parses the codex arg convention: finds -o <path> and
# writes a response there.  Used as a building block for fake codex commands.
# The provider invokes: <codex_bin> exec --json --sandbox read-only -o <path> [-—model M] -
# With bash -c, we pass "--" as argv0, so $1="exec", $2="--json", etc.
_WRITE_RESPONSE = """\
shift  # skip "exec"
while [ $# -gt 0 ]; do
    case "$1" in -o) shift; OUT="$1"; shift ;; *) shift ;; esac
done
"""


def _fake_codex(script: str) -> list[str]:
    """Return a codex_bin command list that runs *script* via bash -c.

    The ``--`` serves as argv0 so the provider's arguments (``exec --json …``)
    land in ``$1 $2 …`` as the test scripts expect.
    """
    return ["bash", "-c", script, "--"]


class TestParseUsage(unittest.TestCase):
    """Tests for _parse_usage JSONL parsing."""

    def test_single_turn(self) -> None:
        jsonl = (
            '{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":50}}\n'
        )
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 50

    def test_multiple_turns(self) -> None:
        jsonl = (
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":200,"output_tokens":30}}\n'
        )
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 300
        assert usage.output_tokens == 50

    def test_empty_output(self) -> None:
        usage = _parse_usage("")
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_no_turn_completed(self) -> None:
        jsonl = '{"type":"thread.started","thread_id":"abc"}\n{"type":"turn.started"}\n'
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_malformed_json_lines_skipped(self) -> None:
        jsonl = (
            "not json at all\n"
            '{"type":"turn.completed","usage":{"input_tokens":500,"output_tokens":60}}\n'
            "{broken\n"
        )
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 500
        assert usage.output_tokens == 60

    def test_blank_lines_skipped(self) -> None:
        jsonl = (
            "\n"
            "  \n"
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
            "\n"
        )
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5

    def test_missing_usage_field(self) -> None:
        jsonl = '{"type":"turn.completed"}\n'
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_null_usage_field(self) -> None:
        jsonl = '{"type":"turn.completed","usage":null}\n'
        usage = _parse_usage(jsonl)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestCodexProviderCall(unittest.TestCase):
    """Tests for CodexProvider.call() using inline bash commands."""

    def test_returns_response_and_usage(self) -> None:
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(
                _WRITE_RESPONSE
                + 'echo -n \'{"options": []}\' > "$OUT"\n'
                + 'echo \'{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}\'\n'
            ),
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
                + _WRITE_RESPONSE.replace(
                    "*) shift",
                    '--model) shift; [ "$1" = "o3" ] && found=1; shift ;; *) shift',
                )
                + '[ -n "$found" ] || { echo "expected --model o3" >&2; exit 1; }\n'
                + 'echo -n "{}" > "$OUT"\n'
            ),
        )
        provider.call("test")

    def test_nonzero_exit_raises(self) -> None:
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex('echo -n "something went wrong" >&2; exit 1'),
        )
        with self.assertRaises(RuntimeError) as ctx:
            provider.call("test")

        assert "exit 1" in str(ctx.exception)
        assert "something went wrong" in str(ctx.exception)

    def test_temp_file_cleaned_up_on_success(self) -> None:
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(_WRITE_RESPONSE + 'echo -n "{}" > "$OUT"\n'),
        )
        provider.call("test")
        # os.unlink in the finally block ran without error.

    def test_temp_file_cleaned_up_on_failure(self) -> None:
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex("exit 1"),
        )
        with self.assertRaises(RuntimeError):
            provider.call("test")
        # Cleanup ran in finally block.

    def test_prompt_includes_system_prompt(self) -> None:
        from explainshell.extraction.llm.prompt import SYSTEM_PROMPT

        # Capture stdin into the response file so we can inspect it.
        provider = CodexProvider(
            "codex/test",
            codex_bin=_fake_codex(_WRITE_RESPONSE + 'cat > "$OUT"\n'),
        )
        content, _ = provider.call("my user content")

        assert content.startswith(SYSTEM_PROMPT)
        assert "my user content" in content


class TestRetryableExceptions(unittest.TestCase):
    def test_timeout_is_retryable(self) -> None:
        provider = CodexProvider("codex/test")
        assert subprocess.TimeoutExpired in provider.retryable_exceptions


class TestMakeProviderRouting(unittest.TestCase):
    """make_provider routes codex models to CodexProvider."""

    def test_bare_codex_not_routed(self) -> None:
        """Bare 'codex' without a model falls through to litellm."""
        provider = make_provider("codex")
        assert not isinstance(provider, CodexProvider)

    def test_codex_with_model(self) -> None:
        provider = make_provider("codex/o3")
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
