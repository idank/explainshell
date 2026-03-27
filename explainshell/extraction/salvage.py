"""Salvage partial results from failed batch extraction runs.

Given a log file from a failed ``extract --batch`` run, this module:

1. Parses the log to find which batches were submitted and which failed.
2. Re-runs the prepare phase to reconstruct the batch grouping.
3. Retrieves partial results from the provider for failed batches.
4. Runs the normal finalize pipeline on recovered results.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Regex patterns for log line parsing.
_RE_SUBMITTED = re.compile(r"batch (\d+)/(\d+) submitted: (\S+)")
_RE_FAILED = re.compile(r"batch (\d+) failed: (.+)")
_RE_ALREADY_STORED = re.compile(r"skipping (\S+) \(already stored\)")


@dataclass
class BatchLogInfo:
    """Parsed information from a batch extraction log."""

    # batch_idx (1-based) -> provider batch ID
    submitted: dict[int, str] = field(default_factory=dict)
    # batch_idx -> error message
    failed: dict[int, str] = field(default_factory=dict)
    # total number of batches (M from "batch N/M submitted: ...")
    total_batches: int = 0
    # source paths that were pre-filtered as "already stored"
    already_stored: set[str] = field(default_factory=set)


def parse_batch_log(log_path: str) -> BatchLogInfo:
    """Parse a log file from a batch extraction run.

    Extracts submitted batch IDs, failed batch indices, and pre-filtered
    (already stored) source paths from the log.
    """
    info = BatchLogInfo()
    with open(log_path) as f:
        for line in f:
            m = _RE_SUBMITTED.search(line)
            if m:
                batch_idx = int(m.group(1))
                total = int(m.group(2))
                batch_id = m.group(3)
                info.submitted[batch_idx] = batch_id
                info.total_batches = max(info.total_batches, total)
                continue

            m = _RE_FAILED.search(line)
            if m:
                batch_idx = int(m.group(1))
                error_msg = m.group(2)
                info.failed[batch_idx] = error_msg
                continue

            m = _RE_ALREADY_STORED.search(line)
            if m:
                info.already_stored.add(m.group(1))
    return info


def salvageable_batches(info: BatchLogInfo) -> dict[int, str]:
    """Return {batch_idx: batch_id} for batches worth attempting salvage on.

    A batch is salvageable if it was submitted (has a batch_id) and failed.
    """
    result: dict[int, str] = {}
    for batch_idx, error_msg in sorted(info.failed.items()):
        batch_id = info.submitted.get(batch_idx)
        if batch_id is None:
            logger.warning(
                "batch %d failed but was never submitted (no batch_id found), skipping",
                batch_idx,
            )
            continue
        result[batch_idx] = batch_id
    return result
