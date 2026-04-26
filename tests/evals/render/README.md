# Markdown render evaluation

This directory contains a lightweight, review-oriented evaluation harness for
changes to `mandoc -T markdown` output.

It is intended to answer:

- Did a markdown-rendering change alter normal, already-good manpages?
- Did rendered HTML structure change in surprising ways?
- Did known compressed option inventories become more structurally useful?

The harness renders a real-manpage corpus with one or more mandoc binaries and
stores three artifacts per page:

- raw markdown (`markdown/*.md`)
- HTML rendered with explainshell's cmark-gfm path (`html/*.html`)
- structural metrics (`metrics/*.json`)

It then compares two render runs and reports metric deltas and suspicious
structural changes.

## Usage

From the repository root:

```bash
source .venv/bin/activate

# Baseline: current vendored mandoc binary.
python tests/evals/render/render_eval.py render \
  --label repo-mandoc \
  --mandoc tools/mandoc-md

# Candidate: patched mandoc tree.
python tests/evals/render/render_eval.py render \
  --label patched-mandoc \
  --mandoc ~/dev/vibe/mandoc-1.14.6/mandoc

# Compare two run directories printed by the render commands.
python tests/evals/render/render_eval.py compare \
  tests/evals/render/runs/<baseline-run> \
  tests/evals/render/runs/<candidate-run>

# Build a Playwright-style screenshot report for suspicious pages.
python tests/evals/render/render_eval.py diff \
  tests/evals/render/runs/<baseline-run> \
  tests/evals/render/runs/<candidate-run>
```

The `diff` command writes `diff-report/index.html` under the current run by
default. It contains expected/actual screenshots with a draggable comparison
slider, plus links to the underlying markdown and rendered HTML artifacts. It
uses `npx playwright screenshot`; if browsers are missing, run:

```bash
npx playwright install chromium
```

Useful `diff` options:

```bash
# Screenshot every page, not only suspicious pages.
python tests/evals/render/render_eval.py diff BASE CURRENT --all

# Limit the report size while iterating.
python tests/evals/render/render_eval.py diff BASE CURRENT --limit 3

# Write report elsewhere.
python tests/evals/render/render_eval.py diff BASE CURRENT --output /tmp/render-diff
```

Use `--fail-on-suspicious` in CI-like usage if suspicious deltas should exit
non-zero:

```bash
python tests/evals/render/render_eval.py compare BASE CURRENT --fail-on-suspicious
```

## Corpus

The corpus is `corpus.txt` — a list of repo-relative manpage paths, one per
line, with `#` comments and blank lines ignored. Paths resolve through the
`explainshell-manpages` git submodule mounted at `manpages/`, so initialize
it once with:

```bash
git submodule update --init
```

The default mix is:

- staple pages that should already render well (`grep`, `sed`, `ssh`, `tar`, …)
- large option-heavy pages (`curl`, `find`, `ps`, `xz`, …)
- known ImageMagick pages where markdown currently collapses option inventories

Add or remove a page by editing `corpus.txt`. To render an ad hoc subset
without touching the corpus, pass paths after `render`:

```bash
python tests/evals/render/render_eval.py render \
  --label imagemagick-only \
  --mandoc ~/dev/vibe/mandoc-1.14.6/mandoc \
  manpages/arch/latest/1/convert.1.gz \
  manpages/arch/latest/1/magick.1.gz
```

## What to inspect

For a visual review, start with `diff-report/index.html` from the `diff`
command — each suspicious page has expected/actual screenshots behind a
draggable slider, with links back to the rendered HTML and raw markdown.

For a numbers-only review, `comparison.md` from `compare` lists suspicious
structural changes and the metric deltas behind them. Prefer rendered HTML
review over raw markdown review when assessing whether content still flows
naturally.

This is a review tool, not a golden snapshot test — it is intentionally not
wired into `make tests-all`. Run it manually when changing
`tools/mandoc-md`, `explainshell/web/markdown.py`, or the
`clean_mandoc_artifacts`/`filter_sections` helpers.
