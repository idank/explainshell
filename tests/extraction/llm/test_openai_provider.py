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
    def test_progress_resets_stall_timer(self, mock_time: MagicMock) -> None:
        """When progress is made, the stall timer resets."""
        stall_timeout = 100
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            # Poll 1: t=0, 2/10
            _make_batch(status="in_progress", completed=2, total=10),
            # Poll 2: t=99, 3/10 (progress! timer resets)
            _make_batch(status="in_progress", completed=3, total=10),
            # Poll 3: t=198, 3/10 (stalled 99s < 100, no cancel yet)
            _make_batch(status="in_progress", completed=3, total=10),
            # Poll 4: t=200, 3/10 (stalled 101s >= 100, cancel)
            _make_batch(status="in_progress", completed=3, total=10),
            _make_batch(status="cancelled", completed=3, total=10),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            times = [99, 99, 2, 1]
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
    def test_cancel_wait_timeout_raises(self, mock_time: MagicMock) -> None:
        """If cancellation itself stalls beyond CANCEL_WAIT_TIMEOUT, raise."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        # Poll 1: t=0, in_progress 5/10
        # Poll 2: t=stall_timeout+1, still 5/10 → cancel issued
        # Poll 3: t=stall_timeout+2, cancelling (status change = progress)
        # Poll 4: t=stall_timeout+2+CANCEL_WAIT_TIMEOUT+1, still cancelling → raise
        poll_results = [
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="cancelling", completed=5, total=10),
            _make_batch(status="cancelling", completed=5, total=10),
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

        with self.assertRaises(ExtractionError) as ctx:
            provider.poll_batch(
                self.client,
                "batch-stuck",
                poll_interval=30,
                stop_event=None,
            )

        self.assertIn("cancellation did not complete", str(ctx.exception))
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
    def test_status_transition_resets_stall_timer(self, mock_time: MagicMock) -> None:
        """Status changes (e.g. validating → in_progress → finalizing)
        count as progress even when request counts don't change."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            # validating for a while (counts stay 0/0)
            _make_batch(status="validating", completed=0, total=10),
            # t=59: transitions to in_progress → timer resets
            _make_batch(status="in_progress", completed=0, total=10),
            # t=118: still 0/0 but only 59s since status change (< 60)
            _make_batch(status="in_progress", completed=0, total=10),
            # t=120: first request done (count change → timer resets)
            _make_batch(status="in_progress", completed=1, total=10),
            # t=179: all done, finalizing → timer resets
            _make_batch(status="finalizing", completed=10, total=10),
            # t=238: still finalizing, 59s (< 60) → no cancel
            _make_batch(status="finalizing", completed=10, total=10),
            # completed
            _make_batch(
                status="completed",
                completed=10,
                total=10,
                output_file_id="file-ok",
            ),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            # Each sleep advances 59s — always under stall_timeout
            jumps = [59, 59, 2, 59, 59, 1]
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

        self.assertEqual(result.status, "completed")
        self.client.batches.cancel.assert_not_called()

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
    def test_cancel_wait_extends_with_continued_progress(
        self, mock_time: MagicMock
    ) -> None:
        """After cancel, if request counts keep increasing the cancel-wait
        timeout resets so we don't discard recoverable results."""
        stall_timeout = 60
        provider = OpenAIProvider("openai/gpt-5-mini", stall_timeout=stall_timeout)
        clock = [0.0]
        mock_time.monotonic = lambda: clock[0]
        mock_time.sleep = MagicMock()

        poll_results = [
            # Stall detected at t=61 → cancel issued
            _make_batch(status="in_progress", completed=5, total=10),
            _make_batch(status="in_progress", completed=5, total=10),
            # After cancel: still cancelling, but more requests completing
            _make_batch(status="cancelling", completed=6, total=10),
            # CANCEL_WAIT_TIMEOUT-1 after last progress → still ok
            _make_batch(status="cancelling", completed=6, total=10),
            # Another request completes → timer resets again
            _make_batch(status="cancelling", completed=7, total=10),
            # Finally cancelled
            _make_batch(
                status="cancelled",
                completed=7,
                total=10,
                output_file_id="file-partial",
            ),
        ]
        self.client.batches.retrieve = MagicMock(side_effect=poll_results)
        self.client.batches.cancel = MagicMock()

        call_idx = [0]

        def advance_clock(*_args: object, **_kwargs: object) -> None:
            jumps = [
                stall_timeout + 1,  # triggers stall → cancel
                1,  # completed 6 (progress)
                CANCEL_WAIT_TIMEOUT - 1,  # close to timeout but progress resets
                1,  # completed 7 (progress again)
                1,  # cancelled
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

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.output_file_id, "file-partial")
        self.client.batches.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
