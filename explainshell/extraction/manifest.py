"""Structured manifest for batch extraction runs.

Written atomically after each batch completes, so it survives crashes.
Thread-safe for parallel batch mode (jobs > 1).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Literal

from pydantic import BaseModel


class BatchManifestEntry(BaseModel):
    """One batch's record in the manifest."""

    batch_idx: int
    batch_id: str | None
    status: Literal["submitted", "completed", "failed"]
    error: str | None
    files: list[str]


class BatchManifest(BaseModel):
    """Top-level manifest schema — used for reading and validation."""

    version: Literal[1]
    model: str
    batch_size: int
    total_batches: int | None
    batches: list[BatchManifestEntry]


class BatchManifestWriter:
    """Thread-safe manifest writer for batch extraction runs.

    Records batch outcomes incrementally and flushes to disk after each update.
    """

    def __init__(self, path: str, model: str, batch_size: int) -> None:
        self._path = path
        self._data = BatchManifest(
            version=1,
            model=model,
            batch_size=batch_size,
            total_batches=None,
            batches=[],
        )
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def set_total_batches(self, n: int) -> None:
        """Set the total number of batches (called once after grouping)."""
        self._data.total_batches = n

    def record_batch(
        self,
        batch_idx: int,
        batch_id: str | None,
        status: Literal["submitted", "completed", "failed"],
        files: list[str],
        error: str | None = None,
    ) -> None:
        """Record a batch outcome and flush to disk.

        If an entry with the same ``batch_idx`` already exists (e.g. from an
        earlier "submitted" record), it is replaced in-place.
        """
        entry = BatchManifestEntry(
            batch_idx=batch_idx,
            batch_id=batch_id,
            status=status,
            error=error,
            files=files,
        )
        with self._lock:
            # Replace existing entry for same batch_idx, or append.
            for i, existing in enumerate(self._data.batches):
                if existing.batch_idx == batch_idx:
                    self._data.batches[i] = entry
                    break
            else:
                self._data.batches.append(entry)
            self._flush()

    def to_dict(self) -> dict:
        """Return manifest data as a plain dict for embedding in reports."""
        with self._lock:
            return self._data.model_dump()

    def _flush(self) -> None:
        """Atomic write: dump to .tmp, then os.replace."""
        data = self._data.model_dump()
        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, self._path)


def load_manifest(path: str, expected_model: str) -> BatchManifest:
    """Read, validate, and return parsed manifest.

    Raises ``pydantic.ValidationError`` on schema errors and ``ValueError``
    if the manifest model doesn't match *expected_model*.
    """
    with open(path) as f:
        raw = json.load(f)
    data = BatchManifest.model_validate(raw)
    if data.model != expected_model:
        raise ValueError(
            f"manifest model {data.model!r} does not match "
            f"requested model {expected_model!r}"
        )
    return data


def failed_batches(data: BatchManifest) -> list[BatchManifestEntry]:
    """Return entries that did not complete successfully.

    Includes status "failed" (batch errored) and "submitted" (process
    died after submit but before completion).
    """
    return [e for e in data.batches if e.status in ("failed", "submitted")]
