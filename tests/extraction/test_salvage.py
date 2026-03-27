"""Tests for explainshell.extraction.salvage — log parsing and batch identification."""

import os
import tempfile
import unittest

from explainshell.extraction.salvage import (
    BatchLogInfo,
    parse_batch_log,
    salvageable_batches,
)


# Sample log lines matching actual format from runner.py / openai provider.
_SAMPLE_LOG = """\
22:44:49 INFO  [explainshell.manager] skipping ubuntu/26.04/1/cpan.1.gz (already stored)
22:44:49 INFO  [explainshell.manager] skipping ubuntu/26.04/1/grub-editenv.1.gz (already stored)
22:45:32 INFO  [explainshell.extraction.runner] collected 5183 request(s) from 5013 file(s) in 104 batch(es)
22:45:34 INFO  [explainshell.extraction.runner] batch 1/5 submitted: batch_abc001
22:45:34 INFO  [explainshell.extraction.runner] batch 2/5 submitted: batch_abc002
22:45:34 INFO  [explainshell.extraction.runner] batch 3/5 submitted: batch_abc003
22:45:34 INFO  [explainshell.extraction.runner] batch 4/5 submitted: batch_abc004
22:45:35 INFO  [explainshell.extraction.runner] batch 5/5 submitted: batch_abc005
22:45:35 INFO  [explainshell.extraction.llm.providers.openai] batch batch_abc001: status=validating (completed=0, failed=0, total=0)
22:46:05 INFO  [explainshell.extraction.llm.providers.openai] batch batch_abc001: status=in_progress (completed=0, failed=0, total=50)
23:10:46 ERROR [explainshell.extraction.runner] batch 2 failed: Batch job expired: batch_abc002
11:37:13 ERROR [explainshell.extraction.runner] batch 4 failed: Batch batch_abc004 cancellation did not complete after 10 minutes
13:40:38 ERROR [explainshell.extraction.runner] batch 5 failed: Batch poll failed after 5 consecutive errors over 602s: Connection error.
16:08:47 INFO  [explainshell.manager] Done: 150 extracted, 10 skipped, 40 failed. Total time: 1000m
"""


class TestParseBatchLog(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        )
        self.tmpfile.write(_SAMPLE_LOG)
        self.tmpfile.close()

    def tearDown(self) -> None:
        os.unlink(self.tmpfile.name)

    def test_parse_submitted_batches(self) -> None:
        info = parse_batch_log(self.tmpfile.name)
        self.assertEqual(len(info.submitted), 5)
        self.assertEqual(info.submitted[1], "batch_abc001")
        self.assertEqual(info.submitted[5], "batch_abc005")

    def test_parse_total_batches(self) -> None:
        info = parse_batch_log(self.tmpfile.name)
        self.assertEqual(info.total_batches, 5)

    def test_parse_failed_batches(self) -> None:
        info = parse_batch_log(self.tmpfile.name)
        self.assertEqual(len(info.failed), 3)
        self.assertIn(2, info.failed)
        self.assertIn(4, info.failed)
        self.assertIn(5, info.failed)
        # Batch 1 and 3 did not fail
        self.assertNotIn(1, info.failed)
        self.assertNotIn(3, info.failed)

    def test_failed_error_messages(self) -> None:
        info = parse_batch_log(self.tmpfile.name)
        self.assertIn("expired", info.failed[2])
        self.assertIn("cancellation", info.failed[4])
        self.assertIn("poll failed", info.failed[5])

    def test_parse_already_stored(self) -> None:
        info = parse_batch_log(self.tmpfile.name)
        self.assertEqual(
            info.already_stored,
            {
                "ubuntu/26.04/1/cpan.1.gz",
                "ubuntu/26.04/1/grub-editenv.1.gz",
            },
        )


class TestSalvageableBatches(unittest.TestCase):
    def test_returns_failed_batches_with_ids(self) -> None:
        info = BatchLogInfo(
            submitted={1: "batch_aaa", 2: "batch_bbb", 3: "batch_ccc"},
            failed={2: "expired", 3: "connection error"},
            total_batches=3,
        )
        result = salvageable_batches(info)
        self.assertEqual(result, {2: "batch_bbb", 3: "batch_ccc"})

    def test_skips_failed_without_submitted_id(self) -> None:
        info = BatchLogInfo(
            submitted={1: "batch_aaa"},
            failed={1: "expired", 99: "some error"},
            total_batches=1,
        )
        result = salvageable_batches(info)
        # batch 99 has no submitted ID, should be skipped
        self.assertEqual(result, {1: "batch_aaa"})

    def test_no_failures(self) -> None:
        info = BatchLogInfo(
            submitted={1: "batch_aaa", 2: "batch_bbb"},
            failed={},
            total_batches=2,
        )
        result = salvageable_batches(info)
        self.assertEqual(result, {})

    def test_empty_log(self) -> None:
        info = BatchLogInfo()
        result = salvageable_batches(info)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
