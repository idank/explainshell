---
name: llm-bench
description: Run the LLM extractor benchmark and compare results. Use when the user wants to benchmark, test, or compare LLM extraction performance on manpages.
user_invocable: true
---

# llm-bench

Run the LLM extractor benchmark tool and compare results against previous runs.

## Usage

```
/llm-bench [--model <model>] [--batch <size>] [-d <description>] [--baseline <path>] [files...]
```

## Arguments

- **model** (optional): LLM model to use. Defaults to `openai/gpt-5-mini`.
- **batch** (optional): Batch size for provider batch API. Defaults to `50`.
- **description** (optional): Short description tag for this run.
- **baseline** (optional): Baseline report path to compare against. Use `list` to find paths. When omitted, compares against the most recent previous report.
- **files** (optional): Specific .gz files or directories. Defaults to the built-in corpus.

## Steps

1. Run the benchmark **in the background** (`run_in_background: true`). The batch API can take 10–30 minutes to complete — do NOT poll the output. Wait for the background task completion notification before proceeding.

```bash
source /home/idank/dev/vibe/explainshell/.venv/bin/activate && python /home/idank/dev/vibe/explainshell/tools/llm_bench.py run --model <model> --batch <size> -d '<description>' [files...]
```

2. Once the background task completes, compare against a report. If the user specified `--baseline`, use `--baseline <path>`. Otherwise omit to compare against the previous report.

```bash
source /home/idank/dev/vibe/explainshell/.venv/bin/activate && python /home/idank/dev/vibe/explainshell/tools/llm_bench.py compare [--baseline <path>]
```

3. Think how changes in the current session (if there are any) could affect the LLM extraction pipeline, and whether the results make sense.

4. Raw LLM responses are stored in the run directory alongside the report. For per-file debugging, inspect the response files directly (e.g. `cat <run-dir>/find.chunk-0.response.txt`).
