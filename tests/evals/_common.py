"""Helpers shared by sibling evals under ``tests/evals/``.

Each eval (render, llm) keeps its own metric set, suspicious-check policy, and
CLI subcommands; this module is just the small kernel of pure helpers — repo
root resolution, corpus reading, run-dir loading, metric lookup, formatting —
that both evals would otherwise duplicate.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = [
    "REPO_ROOT",
    "_safe_name",
    "_repo_relative",
    "_git_metadata",
    "_read_corpus",
    "_load_summary",
    "_page_map",
    "_get_metric",
    "_format_delta",
    "_write_json",
]


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_name(path: str) -> str:
    digest = hashlib.sha1(path.encode()).hexdigest()[:10]
    name = Path(path).name
    for suffix in (".gz", ".1", ".8", ".7", ".5"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "page"
    return f"{clean}-{digest}"


def _read_corpus(corpus_path: Path) -> list[Path]:
    paths: list[Path] = []
    for raw_line in corpus_path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        path = Path(line).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        paths.append(path)
    return paths


def _git_metadata() -> dict[str, Any]:
    def run_git(args: list[str]) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    return {
        "commit": run_git(["rev-parse", "--short", "HEAD"]),
        "dirty": bool(run_git(["status", "--porcelain"])),
    }


def _load_summary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "summary.json").read_text())


def _page_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {page["path"]: page for page in summary["pages"]}


def _get_metric(page: dict[str, Any], key: str) -> int | float:
    """Read a dotted metric path, returning 0 if any segment is missing."""
    value: Any = page.get("metrics", {})
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return 0
        value = value[part]
    if not isinstance(value, int | float) or isinstance(value, bool):
        return 0
    return value


def _format_delta(before: int | float, after: int | float) -> str:
    delta = after - before
    if isinstance(before, float) or isinstance(after, float):
        return f"{before:.2f} -> {after:.2f} ({delta:+.2f})"
    return f"{before} -> {after} ({delta:+})"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
