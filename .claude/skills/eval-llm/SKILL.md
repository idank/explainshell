---
name: eval-llm
description: Evaluate a change to the LLM extraction pipeline against a clean baseline. Use when the user wants to test, validate, or assess a prompt/chunking/post-processing/provider change before committing. Produces a merge / regression / defer verdict.
user_invocable: true
---

# eval-llm

You drive the LLM eval end-to-end against the current working tree, dig into the diff, and decide whether the local changes to `explainshell/extraction/llm/` (or related extraction code) are safe to commit. The flow mirrors `/eval-render`: capture a clean baseline, run the candidate, compare, classify, verdict.

## Usage

```
/eval-llm [--label <tag>] [--model <model>] [--description <text>]
```

## Arguments

- **label** (optional): Short tag for the candidate run dir name. Defaults to a tag inferred from the user's stated change (e.g. `prompt-tweak-v2`, `chunking-fix`). The baseline run is auto-labeled `baseline-clean`.
- **model** (optional): LLM model. Defaults to `openai/gpt-5-mini`. Pick the same model the user has been iterating with — cross-model token deltas are tolerated by the eval but cross-model option-count deltas can muddy the verdict.
- **description** (optional): Long-form description for the candidate run. Defaults to a one-line summary of the user's stated change.

## Step 1: Produce a baseline run and a candidate run

You need two runs: one against the unchanged code and one against the user's change. How the user stages the two states (git stash, commit-and-revert, branch switch, etc.) is their call — don't prescribe.

Run **in the background** (`run_in_background: true`) — typical wall time is 8–15 minutes per run; do not poll. Wait for the task completion notification before kicking off the next one.

```bash
source .venv/bin/activate && \
python tests/evals/llm/llm_eval.py run --label <tag> --model <model> --jobs 10 -d "<one-line summary>"
```

Default to `--jobs 10` without `--batch`. The batch API has high queue latency for our small corpus and serializes when batch_size exceeds chunk count; `--jobs N` parallelizes the realtime API. With 12 corpus pages × ~20 chunks, jobs=10 finishes in ~10 minutes. Only use `--batch` if the user explicitly asks for cost reduction over wall time.

Pick a candidate `<tag>` that reflects the change (e.g. `prompt-tweak-v2`, `chunking-fix`); use `baseline-clean` (or similar) for the baseline. Note both run directory paths from the trailing `Run directory:` line — you'll need them for compare.

If the user already has a baseline they want to reuse (e.g. a recent run from `tests/evals/llm/runs/`), skip the baseline run and use that path. `python tests/evals/llm/llm_eval.py list` shows what's available.

## Step 2: Compare

```bash
source .venv/bin/activate && \
python tests/evals/llm/llm_eval.py compare <baseline-run> <candidate-run>
```

Read the full output. Three sections matter:

- **Aggregate** table: totals at a glance. The verdict scaffold lives here.
- **Per-page metric deltas**: which pages moved and on which axes (`extraction.n_options`, `extraction.n_chunks`, `extraction.plain_text_len`, `tokens.*`).
- **Suspicious structural changes**: pages where the eval flagged a *directional* concern (zero-tolerance on option-count drops, success flips, malformed-options gains).

Tokens print in the deltas section but are deliberately excluded from suspicious checks — model jitter alone moves them ±5%. Do not weight them in the verdict.

## Step 3: Classify every flagged page

For each page in the **Suspicious structural changes** section, decide one of: **improvement**, **regression**, or **ambiguous**. The flag tells you *what* moved; the inspection tells you *why*.

Inspection sources, in order of cheapness:

1. The per-page metric block under "Per-page metric deltas" — does the option count drop track a chunk-count or plain-text-length drop (probably benign: chunking changed, fewer real options to find), or is it a pure drop with everything else stable (probably a real regression in extraction quality)?
2. The candidate response files under `<candidate-run>/responses/<safe-name>.chunk-<N>.response.txt`. Diff against the baseline's `responses/<same-name>` to see which options the model actually returned. Use `diff` directly — these are flat text.
3. The candidate prompts under `<candidate-run>/prompts/<safe-name>.chunk-<N>.prompt.json`. Compare against the baseline if the user's change touched prompt construction.
4. The mandoc-rendered markdown under `<candidate-run>/markdown/<safe-name>.md`. Compare against baseline if the user's change touched chunking or text preparation.

Ask: "do the moves on this page point the same direction, and does that direction match what the user's diff predicts?" When deltas are mixed in a way the change description doesn't explain, classify **ambiguous** rather than guessing.

LLM jitter is real. A single page swinging ±2 options on its own is usually noise. Two or more pages moving together in the same direction, or one page moving by >10% of its option count, is signal.

## Step 4: Apply the rubric

- **merge** ⇢ no flagged pages, or every flagged page classified as improvement, *and* aggregate `total_options` did not drop more than ~1%, *and* `failed_files` / `malformed_options` are flat or down. Recommend the user commit. Suggest a follow-up `/eval-render` if the change touched anything in the rendering / chunking text path.
- **regression** ⇢ any regression-classified page, *or* aggregate `total_options` dropped more than ~1%, *or* `failed_files` / `malformed_options` rose. Tell the user which pages, with before→after numbers and the response-file path you inspected.
- **defer** ⇢ any ambiguous page, or jitter-vs-signal ambiguity the inspection didn't resolve. Ask whether to re-run (LLM determinism is partial; a second baseline+candidate pair often clarifies) or whether the user wants to override.

## Step 5: Report

Final user-facing report (in chat, not a file):

- One-line verdict (**merge** / **regression** / **defer**) and confidence note.
- Aggregate snapshot: `total_options` baseline → candidate, `failed_files` / `malformed_options` deltas, token deltas in parentheses (informational only).
- Run-directory paths for both runs.
- One-liner per flagged page (`<page>: <classification> — <one-line reason>`).
- If **merge**: a one-line commit message draft inferred from the user's change description.
- If **regression**: list the response files you'd want a fix-iteration agent to read first (the worst-regressing 1–2 pages).
- If **defer**: the specific pages/metrics that triggered defer and what evidence would resolve them (typically: a second run, or a manual diff of a specific response file).
