"""Tests for OpenAI provider poll_batch stall detection."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from explainshell.errors import ExtractionError
from explainshell.extraction.llm.providers.openai import (
    CANCEL_WAIT_TIMEOUT,
    OpenAIProvider,
)


def _make_batch(
    status: str = "in_progress",
    completed: int = 0,
    failed: int = 0,
    total: int = 10,
    output_file_id: str | None = None,
) -> SimpleNamespace:
    """Build a fake Batch object matching openai.types.Batch shape."""
    return SimpleNamespace(
        status=status,
        request_counts=SimpleNamespace(
            completed=completed,
            failed=failed,
            total=total,
        ),
        output_file_id=output_file_id,
        usage=None,
        error_file_id=None,
    )


class TestPollBatchStallDetection(unittest.TestCase):
    """poll_batch should cancel the batch when progress stalls."""

    def setUp(self) -> None:
        self.client = MagicMock()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_stall_cancels_and_returns_partial(self, mock_time: MagicMock) -> None:
        """After stall_timeout with no progress, the batch is cancelled and
        the cancelled batch is returned for partial result collection."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]

        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(
                status="cancelled",
                completed=5,
                total=10,
                output_file_id="file-partial",
            ),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            if clock[0] == 0.0:
                clock[0] = stall_timeout + 1
            else:
                clock[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-123",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.output_file_id, "file-partial")
        self.client.batches.cancel.assert_called_once_with("batch-123")

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_progress_does_not_extend_walltime(self, mock_time: MagicMock) -> None:
        """Wall-time limit triggers cancel even when progress is being made."""
        stall_timeout = 100
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            # Poll 1: t=0, 2/10
            _make_batch(status="in_progress", completed=2, total=10),
            # Poll 2: t=99, 3/10 (progress, but doesn't matter)
            _make_batch(status="in_progress", completed=3, total=10),
            # Poll 3: t=101, 5/10 (wall-time exceeded → cancel despite progress)
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="cancelled", completed=5, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            times = [99, 2, 1]
            if call_idx[0] < len(times):
                clock[0] += times[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-x",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "cancelled")
        self.client.batches.cancel.assert_called_once()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_cancel_wait_timeout_returns_batch(self, mock_time: MagicMock) -> None:
        """If cancellation stalls beyond CANCEL_WAIT_TIMEOUT, return batch for partial collection."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        # Poll 1: t=0, in_progress 5/10
        # Poll 2: t=stall_timeout+1, still 5/10 → cancel issued
        # Poll 3: t=stall_timeout+2, cancelling (status change = progress)
        # Poll 4: t=stall_timeout+2+CANCEL_WAIT_TIMEOUT+1, still cancelling → return
        last_batch = _make_batch(status="cancelling", completed=5, total=10)
        poll_results = [
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="cancelling", completed=5, total=10),
            last_batch,
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [stall_timeout + 1, 1, CANCEL_WAIT_TIMEOUT + 1]
            if call_idx[0] < len(jumps):
                clock[0] += jumps[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-stuck",
            poll_interval=30,
            stop_event=None,
        )

        self.assertIs(result, last_batch)
        self.client.batches.cancel.assert_called_once()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_cancel_api_failure_raises(self, mock_time: MagicMock) -> None:
        """If the cancel API call itself fails, raise ExtractionError."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock(side_effect=RuntimeError("API error"))

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            clock[0] += stall_timeout + 1

        mock_time.sleep.side_effect = advance_clock

        with self.assertRaises(ExtractionError) as ctx:
            provider.poll_batch(
                self.client,
                "batch-err",
                poll_interval=30,
                stop_event=None,
            )

        self.assertIn("cancel failed", str(ctx.exception))

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_external_cancel_still_raises(self, mock_time: MagicMock) -> None:
        """A cancelled batch that we did NOT cancel should still raise."""
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=9999)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock(side_effect=lambda _: None)

        poll_results = [
            _make_batch(status="in_progress", completed=3, total=10),
            _make_batch(status="cancelled", completed=3, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)

        with self.assertRaises(ExtractionError) as ctx:
            provider.poll_batch(
                self.client,
                "batch-ext",
                poll_interval=30,
                stop_event=None,
            )

        self.assertIn("cancelled", str(ctx.exception))

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_completed_during_cancel_wait(self, mock_time: MagicMock) -> None:
        """If the batch completes while we're waiting for cancel, return it."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            _make_batch(status="in_progress", completed=9, total=10),
            _make_batch(status="in_progress", completed=9, total=10),
            # After we cancel, it actually completes (last request finished)
            _make_batch(
                status="completed",
                completed=10,
                total=10,
                output_file_id="file-full",
            ),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [stall_timeout + 1, 1]
            if call_idx[0] < len(jumps):
                clock[0] += jumps[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-race",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output_file_id, "file-full")

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_no_stall_completes_normally(self, mock_time: MagicMock) -> None:
        """When progress is steady, the batch completes without cancellation."""
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=1800)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock(
            side_effect=lambda _: clock.__setitem__(0, clock[0] + 5)
        )

        poll_results = [
            _make_batch(status="in_progress", completed=3, total=10),
            _make_batch(status="in_progress", completed=7, total=10),
            _make_batch(
                status="completed",
                completed=10,
                total=10,
                output_file_id="file-ok",
            ),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)

        result = provider.poll_batch(
            self.client,
            "batch-ok",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "completed")
        self.client.batches.cancel.assert_not_called()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_walltime_ignores_status_transitions(self, mock_time: MagicMock) -> None:
        """Status transitions don't affect the wall-time deadline."""
        stall_timeout = 100
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            _make_batch(status="validating", completed=0, total=10),
            # t=50: transitions to in_progress
            _make_batch(status="in_progress", completed=0, total=10),
            # t=90: progress made
            _make_batch(status="in_progress", completed=5, total=10),
            # t=101: wall-time exceeded despite recent progress and transitions
            _make_batch(status="in_progress", completed=8, total=10),
            _make_batch(status="cancelled", completed=8, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [50, 40, 11, 1]
            if call_idx[0] < len(jumps):
                clock[0] += jumps[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-lifecycle",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "cancelled")
        self.client.batches.cancel.assert_called_once()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_long_validating_without_transition_triggers_stall(
        self, mock_time: MagicMock
    ) -> None:
        """A batch stuck in validating with no status or count changes
        should still be detected as stalled."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            _make_batch(status="validating", completed=0, total=0),
            _make_batch(status="validating", completed=0, total=0),
            _make_batch(status="cancelled", completed=0, total=0),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [stall_timeout + 1, 1]
            if call_idx[0] < len(jumps):
                clock[0] += jumps[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-val-stuck",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "cancelled")
        self.client.batches.cancel.assert_called_once()

    @patch("explainshell.extraction.llm.providers.openai.time")
    def test_cancel_wait_measured_from_cancel_time(self, mock_time: MagicMock) -> None:
        """After cancel, the cancel-wait timeout is measured purely from
        the cancel time, regardless of continued progress."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            # Wall-time limit hit at t=61 → cancel issued
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
            # After cancel: requests still completing
            _make_batch(status="cancelling", completed=6, total=10),
            # CANCEL_WAIT_TIMEOUT+1 after cancel → return for partial collection
            _make_batch(status="cancelling", completed=7, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [
                stall_timeout + 1,  # triggers wall-time → cancel
                1,  # completed 6
                CANCEL_WAIT_TIMEOUT + 1,  # exceeds cancel wait → return
            ]
            if call_idx[0] < len(jumps):
                clock[0] += jumps[call_idx[0]]
                call_idx[0] += 1

        mock_time.sleep.side_effect = advance_clock

        result = provider.poll_batch(
            self.client,
            "batch-slow-cancel",
            poll_interval=30,
            stop_event=None,
        )

        self.assertEqual(result.status, "cancelling")
        self.client.batches.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
