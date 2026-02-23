"""
End-to-end regression tests using Playwright.

A Flask dev server is started automatically by the dev_server fixture.
Install dependencies: pip install -r requirements-e2e.txt && playwright install chromium

Workflow
--------
Create / refresh baselines (run once, then commit tests/snapshots/):
    make e2e-update

Compare against baselines (CI / everyday use):
    make e2e

Failure diffs are written to test-results/ (gitignored).
"""

import io
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import Page, expect

SAMPLE_CMD = "tar xzvf archive.tar.gz"
SNAPSHOT_DIR = "tests/e2e/snapshots"
UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS", "").lower() in ("1", "true", "yes")


def assert_screenshot(page: Page, name: str, *, full_page: bool = False, threshold: float = 0.005):
    """Compare a page screenshot against a committed baseline.

    On the first run (or when UPDATE_SNAPSHOTS=1) the baseline is written.
    On subsequent runs the mean per-channel pixel difference must stay
    below `threshold` (fraction of 255) or the test fails.
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    baseline_path = os.path.join(SNAPSHOT_DIR, name)
    actual_bytes = page.screenshot(full_page=full_page)

    if UPDATE_SNAPSHOTS:
        with open(baseline_path, "wb") as f:
            f.write(actual_bytes)
        return

    if not os.path.exists(baseline_path):
        pytest.fail(
            f"No baseline screenshot found at '{baseline_path}'. "
            "Run 'make e2e-update' to generate it, then commit the file."
        )

    baseline = Image.open(baseline_path).convert("RGB")
    actual = Image.open(io.BytesIO(actual_bytes)).convert("RGB")

    if baseline.size != actual.size:
        pytest.fail(
            f"Screenshot '{name}': size changed from {baseline.size} to {actual.size}. "
            "Run 'make e2e-update' to accept the new baseline."
        )

    diff = ImageChops.difference(baseline, actual)
    stat = ImageStat.Stat(diff)
    mean_diff = sum(stat.mean) / (len(stat.mean) * 255.0)

    if mean_diff > threshold:
        os.makedirs("test-results", exist_ok=True)
        stem = os.path.splitext(name)[0]
        Image.open(io.BytesIO(actual_bytes)).save(f"test-results/{stem}-actual.png")
        diff.save(f"test-results/{stem}-diff.png")

        # Build a side-by-side comparison: baseline | actual | diff
        label_h = 20
        w, h = baseline.size
        side_by_side = Image.new("RGB", (w * 3, h + label_h), (255, 255, 255))
        side_by_side.paste(baseline, (0, label_h))
        side_by_side.paste(actual, (w, label_h))
        side_by_side.paste(diff, (w * 2, label_h))

        from PIL import ImageDraw

        draw = ImageDraw.Draw(side_by_side)
        for i, label in enumerate(("Baseline", "Actual", "Diff")):
            draw.text((i * w + 5, 2), label, fill=(0, 0, 0))

        side_by_side.save(f"test-results/{stem}-comparison.png")

        pytest.fail(
            f"Screenshot '{name}' differs from baseline "
            f"(mean diff {mean_diff:.4f} > threshold {threshold}). "
            f"Inspect test-results/{stem}-comparison.png for a side-by-side view. "
            "Run 'make e2e-update' to accept the new look as the baseline."
        )


def _free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session", autouse=True)
def dev_server():
    """Start a local Flask server for the test session."""
    from werkzeug.serving import make_server
    from explainshell.web import app

    app.config["TESTING"] = True

    port = _free_port()
    server = make_server("127.0.0.1", port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"

    # Wait for the server to be ready.
    for _ in range(50):
        try:
            urllib.request.urlopen(base_url, timeout=1)
            break
        except urllib.error.URLError:
            time.sleep(0.1)
    else:
        pytest.fail("Flask test server failed to start")

    yield base_url

    server.shutdown()


def test_homepage_loads(page: Page, dev_server):
    """The home page loads and matches the committed baseline screenshot."""
    page.goto(dev_server)

    expect(page).to_have_title(re.compile(r"explainshell", re.IGNORECASE))
    # The index page replaces the nav search with a full-width input#explain.
    expect(page.locator("input#explain")).to_be_visible()

    assert_screenshot(page, "homepage.png")


def test_explain_sample_command(page: Page, dev_server):
    """Explaining a sample command renders help boxes with borders, lines, and matches baseline."""
    url = f"{dev_server}/explain?cmd={urllib.parse.quote_plus(SAMPLE_CMD)}"
    page.goto(url)
    page.wait_for_load_state("networkidle")

    # Take the screenshot first so the comparison image is always available.
    assert_screenshot(page, "explain-sample.png", full_page=True)

    # Structural assertions — collect all failures for a single clear report.
    errors = []

    if not re.search(re.escape(SAMPLE_CMD), page.title()):
        errors.append(f"Expected page title to contain '{SAMPLE_CMD}', got '{page.title()}'")

    if not page.locator("#command").is_visible():
        errors.append("Expected #command to be visible")

    help_boxes = page.locator("#help .help-box")
    if help_boxes.count() < 1:
        errors.append(
            f"Expected at least one help text row for '{SAMPLE_CMD}', got 0. "
            "The man page data may not be loaded in the database."
        )

    for i in range(help_boxes.count()):
        box = help_boxes.nth(i)
        if not box.is_visible():
            continue
        border = box.evaluate("el => getComputedStyle(el).borderStyle")
        if border != "solid":
            errors.append(f"Help box {i} should have a solid border, got '{border}'")

    svg_paths = page.locator("#canvas path")
    if svg_paths.count() < 1:
        errors.append("Expected SVG connecting lines in the canvas")

    if errors:
        pytest.fail("\n".join(errors))
