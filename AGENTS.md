# Project Instructions — explainshell

A web tool that parses man pages and explains command-line arguments by matching each argument to its help text.

## Tech Stack

- Python 3.12, Flask, SQLite, bashlex, LiteLLM
- Linting: ruff (Python), biome (JS)
- Testing: pytest (unit + doctests + parsing regression), JS Playwright Test (e2e)
- Dependencies: `requirements.txt` (main), `package.json` (Playwright e2e)

## Workflow Requirements

**Before finishing any task**, always:

1. Run `make format; make lint`
1. Run `make tests-all` (unit + e2e + parsing regression) — all tests must pass
   - If e2e tests fail due to snapshot diffs, assess whether the diff is expected, and get user confirmation before running `make e2e-update`
1. Update README.md if the change adds/removes/renames CLI commands, env vars, or user-facing features
1. Update AGENTS.md if the change affects structure, convention, workflow, etc.
1. Provide a draft commit message using Conventional Commits format

### LLM Parsing Regression

When changing prompts, chunking, or other LLM extraction logic, use the LLM regression suite to catch regressions. LLM output is non-deterministic, so the workflow is:

1. `make parsing-update-llm` — generate a baseline with the **current** code
2. Make your prompt/chunking changes
3. `make parsing-regression-llm` — re-extract and compare against the baseline
4. Review the diffs to decide whether differences are regressions or expected improvements

### LLM Benchmarking

Use the benchmark tool (`tools/llm_bench.py`) to compare before/after metrics when making changes to the LLM extractor. It runs extraction on a fixed 10-file corpus and produces a JSON report with aggregate metrics: extracted files, failed files, total options, zero-option pages, multi-chunk pages, and token usage.

**Workflow for code changes (API, prompt, chunking, post-processing):**

```bash
# 1. Stash your changes to get a clean baseline
git stash push -- explainshell/llm_extractor.py

# 2. Run benchmark on the old code
make llm-bench
make llm-bench-baseline

# 3. Restore your changes
git stash pop

# 4. Run benchmark on the new code
make llm-bench

# 5. Compare
make llm-bench-compare
```

**Makefile targets:**

- `make llm-bench` — run benchmark (uses batch API, override model with `MODEL=...`)
- `make llm-bench-baseline` — save the current report as the baseline
- `make llm-bench-compare` — compare current report against baseline, exits non-zero on regressions

**Direct usage** for custom runs (e.g., single file, sequential mode, specific model):

```bash
# Sequential mode (no --batch), single file
python tools/llm_bench.py run --model openai/gpt-5-mini -o report.json path/to/file.1.gz

# Batch mode on the full corpus
python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50 -o report.json tests/regression/manpages/

# Compare two reports
python tools/llm_bench.py compare baseline.json current.json
```

## Environment

- Python virtualenv: repo-local `.venv`
- **CRITICAL**: Every Bash tool call runs in a fresh shell with NO venv active. You MUST prefix every Python/pip/pytest/ruff/make command with `source .venv/bin/activate &&`. Example: `source .venv/bin/activate && make tests`. Never run bare `python`, `pytest`, `ruff`, `pip`, or `make` without activating first.

## Common Commands

```bash
# Run unit tests + doctests (excludes e2e)
make tests

# Run a single test file
pytest tests/test_matcher.py -v

# Run a single test method
pytest tests/test_matcher.py::test_matcher::test_no_options -v

# Lint
make lint

# Format
make format

# Run e2e tests (requires playwright)
make e2e

# Update e2e snapshots
make e2e-update

# Run LLM integration test (requires API key in .env)
make test-llm

# Run parsing regression tests (requires DB)
make parsing-regression

# Update DB to accept current parser output for regression manpages
make parsing-update

# Run LLM parsing regression (requires regression-llm.db)
make parsing-regression-llm

# Regenerate LLM baseline DB (makes API calls, override model with MODEL=...)
make parsing-update-llm

# Run all tests (unit + e2e + parsing regression)
make tests-all

# Run LLM benchmark (makes API calls, override model with MODEL=...)
make llm-bench

# Save current benchmark report as baseline
make llm-bench-baseline

# Compare current benchmark report against baseline
make llm-bench-compare

# Run DB integrity checks
make db-check

# Run web server locally
make serve

# Generate Ubuntu manpage archive (requires Go, RELEASES is required)
make ubuntu-archive RELEASES=questing

# Process a man page into the database
python -m explainshell.manager --mode source /path/to/manpage.1.gz
```

## Project Structure

- `explainshell/` - Main package
  - `manager.py` - CLI entry point for man page processing (`python -m explainshell.manager`)
  - `matcher.py` - Core logic: walks bash AST and matches tokens to help text
  - `store.py` - SQLite storage layer and data classes (ParsedManpage, Option)
  - `errors.py` - Exception hierarchy (ProgramDoesNotExist, DuplicateManpage, InvalidSourcePath, ExtractionError, LowConfidenceError)
  - `llm_extractor.py` - LLM-based option extraction (via LiteLLM)
  - `source_extractor.py` - Direct roff parsing extractor
  - `mandoc_extractor.py` - mandoc -T tree based extractor
  - `tree_parser.py` - Mandoc tree parser with confidence assessment
  - `roff_parser.py` - Roff macro parser (man/mdoc dialects)
  - `manpage.py` - Man page reading and HTML conversion
  - `help_constants.py` - Shell constant definitions for help text
  - `util.py` - Shared utilities (group_continuous, Peekable, name_section)
  - `web/views.py` - Flask routes with URL-based distro/release routing
  - `config.py` - Configuration (DB_PATH, HOST_IP, DEBUG, MANPAGE_URLS)
- `tools/` - Standalone scripts
  - `db_check.py` - DB integrity checker (malformed paths, shadowed duplicates, orphans)
  - `llm_bench.py` - LLM extractor benchmark tool (run/compare metrics reports)
  - `store_extraction.py` - Store LLM-ref extraction JSON into database
- `tests/` - Unit tests (`test_*.py`), fixtures
- `tests/e2e/` - Playwright e2e tests, snapshots, and dedicated `e2e.db`
- `tests/regression/` - Parsing regression tests and manpage .gz fixtures
- `runserver.py` - Flask app entry point
- `manpages/` - Git submodule ([explainshell-manpages](https://github.com/idank/explainshell-manpages))
  - `ubuntu-manpages-operator/` - Go pipeline that fetches Ubuntu `.deb` packages, extracts manpages, and converts them to markdown

## Architecture

### Man Page Processing Pipeline

`manager.py` orchestrates: raw .gz → parse → extract options → store in SQLite.

Extraction modes controlled by `--mode`:
- `--mode source` - Parses roff macros directly via `roff_parser.py` + `source_extractor.py`
- `--mode mandoc` - Uses mandoc -T tree parser via `mandoc_extractor.py`
- `--mode llm:<model>` - Sends man page text to an LLM via LiteLLM (e.g., `llm:gpt-4o`)
- `--mode hybrid:<model>` - Tries mandoc first, falls back to LLM on low confidence

Manager key flags: `--overwrite`, `--dry-run`, `--diff [db|A..B]`, `--debug-dir`, `--drop`, `-j/--jobs <int>` (parallel extraction, default 1)

Flag validation: `--mode` and `--dry-run` cannot be combined with `--diff A..B`; `--drop`, `--overwrite`, and `--diff` are mutually exclusive with each other and with `--dry-run`.

### Data Model (store.py)

SQLite with two tables:
- **manpage** - source (unique basename), name, synopsis, options (JSON), aliases, flags
- **mapping** - command name → manpage id lookup (many-to-one, with score for preference)

Key classes (Pydantic models):
- `Option` - text, short/long flag lists, expects_arg, argument, nested_cmd
- `ParsedManpage` - container with options/arguments properties and `find_option(flag)` lookup

### Command Matching (matcher.py)

Uses bashlex AST visitor pattern:
- `Matcher` inherits from `bashlex.ast.nodevisitor`
- `visitcommand()` - looks up man page, handles multi-command (e.g., `git commit`)
- `visitword()` - matches tokens to options (exact match, then fuzzy split for combined short flags like `-abc`)
- Produces `MatchResult(start, end, text, match)` where start/end are character positions in the original string

### E2E Tests

Hermetic setup: uses a dedicated `tests/e2e/e2e.db` and random port selection. Server is started fresh per run (`reuseExistingServer: false`).
