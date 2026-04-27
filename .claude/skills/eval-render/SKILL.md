---
name: eval-render
description: Evaluate a patched mandoc binary's markdown rendering against the current tools/mandoc-md. Use when the user wants to test, validate, or assess a candidate mandoc build before promoting it. Produces a merge / regression / defer verdict and, on regression, a handoff prompt for an agent in the mandoc source worktree.
user_invocable: true
---

# eval-render

You drive the render eval end-to-end against a candidate mandoc binary, dig into the diff, and decide whether the candidate is safe to promote to `tools/mandoc-md`. On regression, you produce a self-contained prompt the user can hand off to a sibling agent running in the mandoc source worktree.

## Usage

```
/eval-render <candidate-mandoc> [--mandoc-worktree <path>]
```

## Arguments

- **candidate-mandoc** (required): Absolute path to the patched mandoc binary (e.g. `~/dev/vibe/mandoc-1.14.6/mandoc`).
- **mandoc-worktree** (optional): Path to the mandoc source worktree where the candidate was built. Used in the handoff prompt. Inferred from the candidate binary's parent directory if omitted.

## Step 1: Render baseline + candidate

Run both renders sequentially. Each takes 10–30s on the full corpus.

```bash
source .venv/bin/activate && \
python tests/evals/render/render_eval.py render --label baseline-tools-mandoc-md --mandoc tools/mandoc-md
python tests/evals/render/render_eval.py render --label candidate-<short-tag> --mandoc <candidate-mandoc>
```

Pick a `<short-tag>` that distinguishes the candidate (e.g. `paragraphize-fix`, `synopsis-v2`). Note both run directory paths from the trailing `run directory:` line.

## Step 2: Compare

```bash
source .venv/bin/activate && \
python tests/evals/render/render_eval.py compare <baseline-run> <candidate-run>
```

Read the full output. The "Suspicious structural changes" section lists every page where any flagged metric moved.

## Step 3: Classify every flagged page

For each flagged page, decide one of: **improvement**, **regression**, or **ambiguous**.

`compare` already tells you which metrics moved and by how much — that *is* the inspection. The eval's tag set covers links, code, emphasis, headings, table rows, paragraphs, lists, and content length, so anything semantically meaningful surfaces in the deltas. Read the per-page metric block, ask "do these moves all point the same direction, and does that direction make sense for the patch's stated purpose?", and pick the verdict.

When deltas are mixed or surprising in a way the patch description doesn't explain, classify **ambiguous** rather than guessing.

## Step 4: Generate the diff report

Generate the screenshot diff so the user (and you if necessary) can verify visually. Run in background (10 pages × ~10s each).

```bash
source .venv/bin/activate && \
python tests/evals/render/render_eval.py diff <baseline-run> <candidate-run>
```

Use `run_in_background: true`. The diff report path is `<candidate-run>/diff-report/index.html`.

## Step 5: Apply the rubric

- **merge** ⇢ every flagged page classified as improvement. Recommend the user run `cp <candidate-mandoc> tools/mandoc-md` and commit. Suggest running `/eval-llm` as a follow-up gut-check.
- **regression** ⇢ any regression-classified page. Produce the handoff prompt (Step 6).
- **defer** ⇢ any ambiguous page or reviewer uncertainty. Ask the user how to proceed.

## Step 6: Handoff prompt (only on regression)

When the verdict is **regression**, infer the mandoc worktree path (default to the candidate binary's parent directory unless `--mandoc-worktree` was given), then produce a self-contained prompt the user can paste into a sibling Claude session running in that worktree. The prompt must include:

- The exact candidate binary path and how it was invoked.
- The corpus run directories (so the upstream agent can read the produced markdown/HTML).
- Per-regressing-page: the page path, the regressing metrics with before→after values, and a one-line hypothesis about the cause inferred from the metric pattern.
- A pointer to the downstream rendering pipeline so the upstream agent can trace what its markdown becomes: explainshell pipes `mandoc -T markdown` output through `explainshell/web/markdown.py` (CommonMark) before display.
- The success criterion: rebuild mandoc, then re-run `/eval-render <path>` from the explainshell repo and confirm the rubric flips to **merge**.

Format:

````
Hand the user a fenced block they can paste:

```
You're in the mandoc-1.14.6 source tree. Explainshell's render eval flagged the following regressions in your last build at <candidate-mandoc>:

**Regressing pages**:
- `<page-path>`:
  - <metric>: <before> -> <after>
  - Likely cause: <one-line hypothesis from the metric pattern>

[repeat per page]

**Rendering pipeline**: explainshell renders the `-T markdown` output through CommonMark (`explainshell/web/markdown.py`) before display, so anything CommonMark interprets specially (4-space indent → code block, blank lines → paragraph break, etc.) shapes the final HTML. Read the candidate markdown for the regressing pages to understand why the metric moved.

**Reference artifacts** (read-only):
- baseline markdown/HTML: <baseline-run>/{markdown,html}/
- candidate markdown/HTML: <candidate-run>/{markdown,html}/

**Iterate**:
1. Make a fix in the mandoc source.
2. `make` to rebuild.
3. From <explainshell-repo>, run `/eval-render <candidate-mandoc>`.
4. Stop when the verdict flips to "merge".
```
````

Substitute the actual values when producing the block. Keep regression-page details concrete: real file paths, real metric numbers.

## Step 7: Report

Final user-facing report (in chat, not a file):

- One-line verdict (**merge** / **regression** / **defer**) and confidence note.
- Corpus stats: total pages, flagged pages broken down by classification.
- Run-directory paths and diff-report URL.
- One-liner per flagged page (`<page>: <classification> — <one-line reason>`).
- If **merge**: the exact `cp` + `git commit` commands to promote.
- If **regression**: the handoff prompt fenced block.
- If **defer**: the specific pages/metrics that triggered defer and what evidence would resolve them.
