#!/usr/bin/env python3
"""Evaluate mandoc markdown rendering on a real-manpage corpus.

This is intentionally a review tool, not a golden snapshot test.  It renders a
corpus with a chosen mandoc binary, saves markdown/HTML/metrics artifacts, and
compares two runs for structural changes that deserve human inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from html import escape as html_escape
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from explainshell import config  # noqa: E402
from explainshell.extraction.llm.text import (  # noqa: E402
    clean_mandoc_artifacts,
    filter_sections,
)
from explainshell.web.markdown import render_markdown  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = EVAL_DIR / "corpus.txt"
DEFAULT_RUNS_DIR = EVAL_DIR / "runs"
DEFAULT_DIFF_DIRNAME = "diff-report"

_TAGS = (
    "a",
    "blockquote",
    "br",
    "code",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "pre",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
)
_OPTION_RE = re.compile(r"(?<![\w\\])(?:\\?-{1,2}|\\\[mi\])[-A-Za-z0-9][-_A-Za-z0-9]*")


class TagCounter(HTMLParser):
    """Tiny HTML structural counter using the standard library parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.data_chars = 0
        self.max_depth = 0
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] += 1
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] += 1

    def handle_endtag(self, tag: str) -> None:
        self._depth = max(self._depth - 1, 0)

    def handle_data(self, data: str) -> None:
        self.data_chars += len(data)


@dataclass(frozen=True)
class RenderedPage:
    path: str
    safe_name: str
    markdown: str
    html: str
    metrics: dict[str, Any]


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


def _mandoc_markdown(mandoc_path: str, manpage: Path) -> str:
    result = subprocess.run(
        [mandoc_path, "-T", "markdown", str(manpage)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"mandoc exited {result.returncode}"
        )
    if not result.stdout.strip():
        raise RuntimeError("mandoc produced empty markdown")
    return result.stdout.rstrip() + "\n"


def _line_metrics(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    line_lengths = [len(line) for line in lines]
    option_counts = [len(_OPTION_RE.findall(line)) for line in lines]
    return {
        "line_count": len(lines),
        "nonblank_line_count": sum(1 for line in lines if line.strip()),
        "max_line_length": max(line_lengths, default=0),
        "avg_line_length": round(sum(line_lengths) / len(lines), 2) if lines else 0,
        "giant_lines_500": sum(1 for length in line_lengths if length > 500),
        "giant_lines_1000": sum(1 for length in line_lengths if length > 1000),
        "option_like_tokens": sum(option_counts),
        "max_option_like_tokens_on_line": max(option_counts, default=0),
        "lines_with_multiple_option_like_tokens": sum(
            1 for count in option_counts if count >= 2
        ),
    }


def _html_metrics(html: str) -> dict[str, Any]:
    parser = TagCounter()
    parser.feed(html)
    tags = {tag: parser.tags.get(tag, 0) for tag in _TAGS}
    return {
        "tags": tags,
        "data_chars": parser.data_chars,
        "max_depth": parser.max_depth,
    }


def _filtered_metrics(markdown: str) -> dict[str, Any]:
    cleaned = clean_mandoc_artifacts(markdown.strip())
    filtered, removed = filter_sections(cleaned)
    return {
        "line_count": len(filtered.splitlines()),
        "char_count": len(filtered),
        "removed_sections": removed,
    }


def _metrics(path: str, markdown: str, html: str) -> dict[str, Any]:
    return {
        "path": path,
        "markdown": _line_metrics(markdown),
        "filtered": _filtered_metrics(markdown),
        "html": _html_metrics(html),
    }


def _render_page(mandoc_path: str, manpage: Path) -> RenderedPage:
    rel_path = _repo_relative(manpage)
    markdown = _mandoc_markdown(mandoc_path, manpage)
    html = render_markdown(markdown)
    return RenderedPage(
        path=rel_path,
        safe_name=_safe_name(rel_path),
        markdown=markdown,
        html=html,
        metrics=_metrics(rel_path, markdown, html),
    )


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def render_run(args: argparse.Namespace) -> int:
    mandoc_path = os.path.expanduser(args.mandoc or config.MANDOC_PATH)
    corpus = [Path(p).expanduser() for p in args.paths]
    if not corpus:
        corpus = _read_corpus(Path(args.corpus))
    corpus = [p if p.is_absolute() else REPO_ROOT / p for p in corpus]

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label).strip("-") or "render"
    run_dir = Path(args.output or DEFAULT_RUNS_DIR / f"{timestamp}-{label}")
    for subdir in ("markdown", "html", "metrics"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for manpage in corpus:
        rel_path = _repo_relative(manpage)
        print(f"rendering {rel_path}")
        if not manpage.is_file():
            failures.append({"path": rel_path, "error": "file not found"})
            continue
        try:
            page = _render_page(mandoc_path, manpage)
        except Exception as exc:  # noqa: BLE001 - report per-page render failures.
            failures.append({"path": rel_path, "error": str(exc)})
            continue
        (run_dir / "markdown" / f"{page.safe_name}.md").write_text(page.markdown)
        (run_dir / "html" / f"{page.safe_name}.html").write_text(page.html)
        _write_json(run_dir / "metrics" / f"{page.safe_name}.json", page.metrics)
        pages.append(
            {
                "path": page.path,
                "safe_name": page.safe_name,
                "metrics": page.metrics,
            }
        )

    summary = {
        "label": args.label,
        "timestamp": datetime.now(UTC).isoformat(),
        "mandoc": mandoc_path,
        "git": _git_metadata(),
        "corpus": [_repo_relative(path) for path in corpus],
        "page_count": len(pages),
        "failure_count": len(failures),
        "failures": failures,
        "pages": pages,
    }
    _write_json(run_dir / "summary.json", summary)
    print(f"\nrun directory: {run_dir}")
    print(f"rendered pages: {len(pages)}")
    print(f"failures: {len(failures)}")
    return 1 if failures and args.fail_on_failure else 0


def _load_summary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "summary.json").read_text())


def _page_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {page["path"]: page for page in summary["pages"]}


def _get_metric(page: dict[str, Any], key: str) -> int | float:
    """Read a dotted metric path, returning 0 if any segment is missing.

    Older runs may not contain every key the current schema tracks; treating
    them as 0 keeps cross-version comparisons running instead of crashing.
    """
    value: Any = page.get("metrics", {})
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return 0
        value = value[part]
    if not isinstance(value, int | float):
        return 0
    return value


def _suspicious_changes(
    path: str, old: dict[str, Any] | None, new: dict[str, Any] | None
) -> list[str]:
    if old is None:
        return ["page added"]
    if new is None:
        return ["page removed"]

    reasons: list[str] = []
    # All thresholds 0.0: any non-zero delta surfaces the page.  Cheap
    # structural metrics rarely move by accident, so false positives are
    # tolerable and small structural improvements are exactly what we want
    # flagged.
    checks = {
        "markdown.max_line_length": 0.0,
        "markdown.giant_lines_500": 0.0,
        "markdown.giant_lines_1000": 0.0,
        "markdown.max_option_like_tokens_on_line": 0.0,
        "filtered.line_count": 0.0,
        "html.tags.blockquote": 0.0,
        "html.tags.br": 0.0,
        "html.tags.li": 0.0,
        "html.tags.pre": 0.0,
        "html.tags.ul": 0.0,
        "html.tags.ol": 0.0,
        "html.tags.p": 0.0,
    }
    for key, tolerance in checks.items():
        before = _get_metric(old, key)
        after = _get_metric(new, key)
        delta = after - before
        if delta == 0:
            continue
        if tolerance == 0.0:
            reasons.append(f"{key}: {before} -> {after} ({delta:+})")
            continue
        denominator = max(abs(before), 1)
        if abs(delta) / denominator > tolerance:
            reasons.append(f"{key}: {before} -> {after} ({delta:+})")

    old_removed = old["metrics"]["filtered"]["removed_sections"]
    new_removed = new["metrics"]["filtered"]["removed_sections"]
    if old_removed != new_removed:
        reasons.append(
            f"filtered.removed_sections changed: {old_removed} -> {new_removed}"
        )

    return reasons


def _format_delta(before: int | float, after: int | float) -> str:
    delta = after - before
    if isinstance(before, float) or isinstance(after, float):
        return f"{before:.2f} -> {after:.2f} ({delta:+.2f})"
    return f"{before} -> {after} ({delta:+})"


def _changed_metric_lines(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    metric_keys = [
        "markdown.line_count",
        "markdown.max_line_length",
        "markdown.giant_lines_500",
        "markdown.max_option_like_tokens_on_line",
        "filtered.line_count",
        "html.tags.p",
        "html.tags.br",
        "html.tags.li",
        "html.tags.blockquote",
        "html.tags.pre",
    ]
    changed: list[str] = []
    for key in metric_keys:
        before = _get_metric(old, key)
        after = _get_metric(new, key)
        if before != after:
            changed.append(f"- `{key}`: {_format_delta(before, after)}")
    return changed


def _summarize_comparison(
    base_dir: Path,
    current_dir: Path,
    baseline: dict[str, Any],
    current: dict[str, Any],
    suspicious: dict[str, list[str]],
    paths: list[str],
) -> str:
    base_pages = _page_map(baseline)
    current_pages = _page_map(current)
    lines: list[str] = [
        "# Markdown render comparison",
        "",
        f"Baseline: `{base_dir}` (`{baseline['label']}`)",
        f"Current: `{current_dir}` (`{current['label']}`)",
        "",
        "## Summary",
        "",
        f"- baseline pages: {len(base_pages)}",
        f"- current pages: {len(current_pages)}",
        f"- baseline failures: {baseline['failure_count']}",
        f"- current failures: {current['failure_count']}",
        "",
        "## Suspicious structural changes",
        "",
    ]

    if suspicious:
        for path, reasons in suspicious.items():
            lines.append(f"### `{path}`")
            lines.append("")
            for reason in reasons:
                lines.append(f"- {reason}")
            lines.append("")
    else:
        lines.append("No suspicious structural changes detected.")
        lines.append("")

    lines.extend(["## Metric deltas", ""])
    for path in paths:
        old = base_pages.get(path)
        new = current_pages.get(path)
        if old is None or new is None:
            continue
        changed = _changed_metric_lines(old, new)
        if changed:
            lines.append(f"### `{path}`")
            lines.append("")
            lines.extend(changed)
            lines.append("")

    return "\n".join(lines)


def _comparison_data(
    base_dir: Path, current_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, list[str]]]:
    baseline = _load_summary(base_dir)
    current = _load_summary(current_dir)
    base_pages = _page_map(baseline)
    current_pages = _page_map(current)
    paths = sorted(set(base_pages) | set(current_pages))
    suspicious: dict[str, list[str]] = {}
    for path in paths:
        reasons = _suspicious_changes(
            path, base_pages.get(path), current_pages.get(path)
        )
        if reasons:
            suspicious[path] = reasons
    return baseline, current, paths, suspicious


def compare_runs(args: argparse.Namespace) -> int:
    base_dir = Path(args.baseline)
    current_dir = Path(args.current)
    baseline, current, paths, suspicious = _comparison_data(base_dir, current_dir)
    report = _summarize_comparison(
        base_dir, current_dir, baseline, current, suspicious, paths
    )
    report_path = current_dir / "comparison.md"
    report_path.write_text(report)
    print(report)
    print(f"comparison report: {report_path}")

    if suspicious and args.fail_on_suspicious:
        return 1
    return 0


def _artifact_path(run_dir: Path, page: dict[str, Any], kind: str, suffix: str) -> Path:
    return run_dir / kind / f"{page['safe_name']}.{suffix}"


def _pad_screenshots_to_match(expected: Path, actual: Path) -> None:
    """Top-pad the shorter screenshot with white pixels so both PNGs share
    the same dimensions.  Lets the comparison slider clip cleanly without the
    taller image bleeding past the divider.
    """
    from PIL import Image

    with Image.open(expected) as e_img, Image.open(actual) as a_img:
        target_w = max(e_img.width, a_img.width)
        target_h = max(e_img.height, a_img.height)
        for path, img in ((expected, e_img), (actual, a_img)):
            if img.width == target_w and img.height == target_h:
                continue
            canvas = Image.new("RGB", (target_w, target_h), "white")
            canvas.paste(img.convert("RGB"), (0, 0))
            canvas.save(path)


def _write_review_page(
    path: Path,
    *,
    title: str,
    source_path: str,
    label: str,
    fragment_html: str,
) -> None:
    del source_path, label  # diff index already shows path + which side this is
    path.write_text(
        f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{html_escape(title)}</title>
<style>
  body {{
    margin: 0;
    background: #fff;
    color: #111;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  main {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
  pre, code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  pre {{ overflow-x: auto; background: #f6f8fa; padding: 12px; border-radius: 4px; }}
  code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 3px; }}
  pre code {{ background: none; padding: 0; }}
  table {{ border-collapse: collapse; }}
  td, th {{ border: 1px solid #e5e7eb; padding: 4px 8px; }}
</style>
</head>
<body>
<main>
{fragment_html}
</main>
</body>
</html>
"""
    )


_CLIP_HEIGHT = 12000


def _screenshot_page(
    html_path: Path,
    png_path: Path,
    *,
    timeout_ms: int,
    clip_height: int | None = None,
) -> None:
    # Hard ceiling so a stalled npx (e.g. cold install) can't hang the run.
    subprocess_timeout = max(timeout_ms / 1000 * 3, 60)
    cmd = [
        "npx",
        "playwright",
        "screenshot",
        "--browser",
        "chromium",
        "--timeout",
        str(timeout_ms),
    ]
    if clip_height is None:
        cmd += ["--full-page", "--viewport-size=1280,900"]
    else:
        # Dropping --full-page makes Playwright capture only the viewport,
        # giving us a top-clipped screenshot that stays under Chromium's
        # full-page capture limits.
        cmd += [f"--viewport-size=1280,{clip_height}"]
    cmd += [html_path.resolve().as_uri(), str(png_path)]
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"playwright screenshot timed out after {subprocess_timeout:.0f}s"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "playwright screenshot failed; run `npx playwright install chromium` "
            f"if browsers are missing. Details: {detail}"
        )


def _screenshot_with_clip_fallback(
    html_path: Path, png_path: Path, *, timeout_ms: int
) -> None:
    try:
        _screenshot_page(html_path, png_path, timeout_ms=timeout_ms)
    except RuntimeError:
        print(f"    full-page failed, retrying with clip-height {_CLIP_HEIGHT}")
        _screenshot_page(
            html_path, png_path, timeout_ms=timeout_ms, clip_height=_CLIP_HEIGHT
        )


def _rel(from_dir: Path, target: Path) -> str:
    return os.path.relpath(target, from_dir).replace(os.sep, "/")


def _write_diff_index(
    report_dir: Path,
    *,
    baseline: dict[str, Any],
    current: dict[str, Any],
    cards: list[dict[str, Any]],
) -> None:
    cards_html: list[str] = []
    options_html: list[str] = []
    for index, card in enumerate(cards):
        reasons = (
            ", ".join(html_escape(r) for r in card["reasons"]) or "Included by --all"
        )
        metric_items = [
            html_escape(line.removeprefix("- `").replace("`: ", ": "))
            for line in card["metric_lines"]
        ]
        metrics = (
            "".join(f"<li>{m}</li>" for m in metric_items)
            or "<li>No tracked metric deltas</li>"
        )
        card_id = f"card-{card['safe_name']}"
        active_attr = " active" if index == 0 else ""
        options_html.append(
            f'<option value="{html_escape(card_id)}">'
            f"{html_escape(card['path'])}</option>"
        )
        cards_html.append(
            f"""
<section id=\"{html_escape(card_id)}\" class=\"card{active_attr}\">
  <h2>{html_escape(card["path"])}</h2>
  <div class=\"meta\">
    <p><b>Why flagged:</b> {reasons}</p>
    <details><summary>Metric deltas</summary><ul>{metrics}</ul></details>
  </div>
  <nav class=\"tabs\" role=\"tablist\">
    <button data-mode=\"slider\" aria-selected=\"true\">Slider</button>
    <button data-mode=\"side-by-side\">Side by side</button>
    <button data-mode=\"actual\">Actual</button>
    <button data-mode=\"expected\">Expected</button>
  </nav>
  <div class=\"view view-slider active\">
    <img-comparison-slider hover=\"false\" tabindex=\"0\">
      <img slot=\"first\" src=\"{html_escape(card["expected_png"])}\" alt=\"Expected screenshot\">
      <img slot=\"second\" src=\"{html_escape(card["actual_png"])}\" alt=\"Actual screenshot\">
    </img-comparison-slider>
  </div>
  <div class=\"view view-side-by-side\">
    <div><div class=\"side-label\">expected</div><img src=\"{html_escape(card["expected_png"])}\" alt=\"Expected screenshot\"></div>
    <div><div class=\"side-label\">actual</div><img src=\"{html_escape(card["actual_png"])}\" alt=\"Actual screenshot\"></div>
  </div>
  <div class=\"view view-actual\"><img src=\"{html_escape(card["actual_png"])}\" alt=\"Actual screenshot\"></div>
  <div class=\"view view-expected\"><img src=\"{html_escape(card["expected_png"])}\" alt=\"Expected screenshot\"></div>
  <p class=\"links\">
    <a href=\"{html_escape(card["expected_html"])}\">expected HTML</a>
    <a href=\"{html_escape(card["actual_html"])}\">actual HTML</a>
    <a href=\"{html_escape(card["expected_md"])}\">expected markdown</a>
    <a href=\"{html_escape(card["actual_md"])}\">actual markdown</a>
  </p>
</section>
"""
        )

    report_dir.joinpath("index.html").write_text(
        f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Mandoc markdown render diff</title>
<link rel=\"stylesheet\" href=\"https://unpkg.com/img-comparison-slider@8/dist/styles.css\">
<script defer src=\"https://unpkg.com/img-comparison-slider@8/dist/index.js\"></script>
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #fff;
  color: #111;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}}
header {{
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid #e5e7eb;
  flex-wrap: wrap;
}}
header h1 {{ margin: 0; font-size: 16px; font-weight: 600; }}
.summary {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.pill {{
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: #555;
}}
.pill b {{ color: #111; font-weight: 600; }}
main {{ max-width: 1320px; margin: 0 auto; padding: 24px 24px 64px; }}
#page-select {{
  font: inherit;
  font-size: 13px;
  padding: 4px 8px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #fff;
  max-width: 100%;
  flex: 1 1 320px;
  min-width: 0;
}}
.card {{ display: none; margin-bottom: 0; }}
.card.active {{ display: block; }}
.empty {{ color: #555; }}
.card h2 {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 6px;
  word-break: break-all;
  color: #111;
}}
.meta {{ font-size: 13px; color: #444; margin-bottom: 12px; }}
.meta p {{ margin: 2px 0; }}
.meta details {{ margin-top: 4px; }}
.meta summary {{ cursor: pointer; color: #555; }}
.meta ul {{ margin: 4px 0 0; padding-left: 20px; color: #555; }}
.tabs {{
  display: flex;
  border-bottom: 1px solid #e5e7eb;
  margin-bottom: 12px;
}}
.tabs button {{
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  padding: 8px 14px;
  font-size: 13px;
  font-family: inherit;
  color: #555;
  cursor: pointer;
}}
.tabs button[aria-selected="true"] {{
  color: #111;
  border-bottom-color: #111;
}}
.view {{ display: none; }}
.view.active {{ display: block; }}
.view img {{ display: block; width: 100%; height: auto; border: 1px solid #e5e7eb; }}
.view-side-by-side.active {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.side-label {{ font-size: 12px; color: #555; margin-bottom: 4px; }}
img-comparison-slider {{
  display: block;
  width: 100%;
  border: 1px solid #e5e7eb;
  --divider-color: #2563eb;
  --divider-width: 2px;
  --default-handle-color: #2563eb;
}}
img-comparison-slider img {{ display: block; width: 100%; height: auto; }}
.links {{ font-size: 13px; margin: 12px 0 0; }}
.links a {{ color: #2563eb; margin-right: 16px; text-decoration: none; }}
.links a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
  <h1>Render diff</h1>
  <div class=\"summary\">
    <span class=\"pill\">expected <b>{html_escape(baseline["label"])}</b></span>
    <span class=\"pill\">actual <b>{html_escape(current["label"])}</b></span>
  </div>
  {"<select id='page-select' aria-label='Select page'>" + "".join(options_html) + "</select>" if cards else ""}
  {f'<span class="pill"><span id="page-pos">1</span> / {len(cards)}</span>' if cards else ""}
</header>
<main>
{"".join(cards_html) if cards_html else "<p class='empty'>No pages selected for screenshot diff.</p>"}
</main>
<script>
document.addEventListener("click", e => {{
  const btn = e.target.closest(".tabs button");
  if (!btn) return;
  const card = btn.closest(".card");
  const mode = btn.dataset.mode;
  card.querySelectorAll(".tabs button").forEach(b =>
    b.setAttribute("aria-selected", b === btn ? "true" : "false"));
  card.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.classList.contains("view-" + mode)));
}});

const select = document.getElementById("page-select");
const pos = document.getElementById("page-pos");
function showCard(id, {{ updateHash = true }} = {{}}) {{
  const card = id && document.getElementById(id);
  if (!card) return false;
  document.querySelectorAll(".card").forEach(c =>
    c.classList.toggle("active", c === card));
  if (select) select.value = id;
  if (pos) pos.textContent = String(select.selectedIndex + 1);
  if (updateHash && location.hash.slice(1) !== id) {{
    history.replaceState(null, "", "#" + id);
  }}
  return true;
}}
if (select) {{
  select.addEventListener("change", () => showCard(select.value));
  const initial = location.hash.slice(1);
  if (!initial || !showCard(initial, {{ updateHash: false }})) {{
    showCard(select.value, {{ updateHash: false }});
  }}
  window.addEventListener("hashchange", () =>
    showCard(location.hash.slice(1) || select.options[0].value, {{ updateHash: false }}));
}}
</script>
</body>
</html>
"""
    )


def diff_report(args: argparse.Namespace) -> int:
    base_dir = Path(args.baseline)
    current_dir = Path(args.current)
    baseline, current, paths, suspicious = _comparison_data(base_dir, current_dir)
    base_pages = _page_map(baseline)
    current_pages = _page_map(current)
    report_dir = Path(args.output or current_dir / DEFAULT_DIFF_DIRNAME)
    pages_dir = report_dir / "pages"
    shots_dir = report_dir / "screenshots"
    pages_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    selected = paths if args.all else list(suspicious)
    if args.limit is not None:
        selected = selected[: args.limit]

    cards: list[dict[str, Any]] = []
    for path in selected:
        old = base_pages.get(path)
        new = current_pages.get(path)
        if old is None or new is None:
            side = "baseline" if new is None else "current"
            print(f"skipping {path}: only present in {side}")
            continue
        safe_name = new["safe_name"]
        expected_fragment = _artifact_path(base_dir, old, "html", "html").read_text()
        actual_fragment = _artifact_path(current_dir, new, "html", "html").read_text()
        expected_page = pages_dir / f"{safe_name}-expected.html"
        actual_page = pages_dir / f"{safe_name}-actual.html"
        expected_png = shots_dir / f"{safe_name}-expected.png"
        actual_png = shots_dir / f"{safe_name}-actual.png"
        _write_review_page(
            expected_page,
            title=f"expected: {path}",
            source_path=path,
            label=f"expected / {baseline['label']}",
            fragment_html=expected_fragment,
        )
        _write_review_page(
            actual_page,
            title=f"actual: {path}",
            source_path=path,
            label=f"actual / {current['label']}",
            fragment_html=actual_fragment,
        )
        try:
            print(f"screenshot {path} expected")
            _screenshot_with_clip_fallback(
                expected_page, expected_png, timeout_ms=args.timeout
            )
            print(f"screenshot {path} actual")
            _screenshot_with_clip_fallback(
                actual_page, actual_png, timeout_ms=args.timeout
            )
        except RuntimeError as exc:
            print(f"  ! skipping {path}: {exc}")
            continue
        _pad_screenshots_to_match(expected_png, actual_png)
        cards.append(
            {
                "path": path,
                "safe_name": safe_name,
                "reasons": suspicious.get(path, []),
                "metric_lines": _changed_metric_lines(old, new),
                "expected_png": _rel(report_dir, expected_png),
                "actual_png": _rel(report_dir, actual_png),
                "expected_html": _rel(report_dir, expected_page),
                "actual_html": _rel(report_dir, actual_page),
                "expected_md": _rel(
                    report_dir, _artifact_path(base_dir, old, "markdown", "md")
                ),
                "actual_md": _rel(
                    report_dir, _artifact_path(current_dir, new, "markdown", "md")
                ),
            }
        )

    comparison = _summarize_comparison(
        base_dir, current_dir, baseline, current, suspicious, paths
    )
    (report_dir / "comparison.md").write_text(comparison)
    _write_diff_index(report_dir, baseline=baseline, current=current, cards=cards)
    print(f"diff report: {report_dir / 'index.html'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_p = subparsers.add_parser("render", help="render corpus artifacts")
    render_p.add_argument("paths", nargs="*", help="optional manpage paths")
    render_p.add_argument("--label", required=True, help="human-readable run label")
    render_p.add_argument(
        "--mandoc",
        help=f"mandoc binary (default: MANDOC_PATH/config, currently {config.MANDOC_PATH})",
    )
    render_p.add_argument(
        "--corpus", default=str(DEFAULT_CORPUS), help="corpus file path"
    )
    render_p.add_argument("--output", help="run output directory")
    render_p.add_argument(
        "--fail-on-failure",
        action="store_true",
        help="exit non-zero on render failures",
    )
    render_p.set_defaults(func=render_run)

    compare_p = subparsers.add_parser("compare", help="compare two render runs")
    compare_p.add_argument("baseline", help="baseline run directory")
    compare_p.add_argument("current", help="current run directory")
    compare_p.add_argument(
        "--fail-on-suspicious",
        action="store_true",
        help="exit non-zero when suspicious structural changes are detected",
    )
    compare_p.set_defaults(func=compare_runs)

    diff_p = subparsers.add_parser(
        "diff", help="build a Playwright-style screenshot diff report"
    )
    diff_p.add_argument("baseline", help="expected/baseline run directory")
    diff_p.add_argument("current", help="actual/current run directory")
    diff_p.add_argument("--output", help="diff report output directory")
    diff_p.add_argument(
        "--all",
        action="store_true",
        help="screenshot all pages instead of only suspicious pages",
    )
    diff_p.add_argument(
        "--limit", type=int, help="maximum number of pages to screenshot"
    )
    diff_p.add_argument(
        "--timeout",
        type=int,
        default=30_000,
        help="Playwright screenshot timeout in milliseconds",
    )
    diff_p.set_defaults(func=diff_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
