"""Structured report for extraction runs.

Written to the run directory as ``report.json`` after extraction completes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class GitInfo(BaseModel):
    commit: str | None
    commit_short: str | None
    dirty: bool | None


class ExtractConfig(BaseModel):
    mode: str | None = None
    model: str | None = None
    overwrite: bool = False
    drop: bool = False
    jobs: int = 1
    batch_size: int | None = None
    debug: bool = False


class ExtractSummary(BaseModel):
    succeeded: int
    skipped: int
    failed: int
    prefilter_skipped: int = 0
    symlinks_mapped: int = 0
    interrupted: bool = False
    fatal_error: str | None = None


class DbCounts(BaseModel):
    manpages: int
    mappings: int


class ExtractionReport(BaseModel):
    version: Literal[1] = 1
    command: Literal["extract"] = "extract"
    timestamp: str
    git: GitInfo
    config: ExtractConfig
    elapsed_seconds: float
    summary: ExtractSummary
    db_before: DbCounts | None = None
    db_after: DbCounts | None = None
    batch_manifest: dict[str, Any] | None = None
