# Project Instructions — explainshell

A web tool that parses man pages and explains command-line arguments by matching each argument to its help text.

## Tech Stack

- Python 3.12, Flask, SQLite, bashlex, OpenAI SDK, Google Gemini SDK, LiteLLM (fallback)
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

### LLM Benchmarking

Use the benchmark tool (`tools/llm_bench.py`) to compare before/after metrics when making changes to the LLM extractor. It runs extraction on a default 10-file corpus (`tests/regression/llm-bench/manpages/`) and produces a JSON report with aggregate metrics: extracted files, failed files, total options, zero-option pages, multi-chunk pages, and token usage. Reports are auto-saved with timestamps to `tests/regression/llm-bench/` and include git metadata (commit, dirty state).

Each run accepts an optional `-d "..."` to label what this run represents. When running benchmarks, always provide a description inferred from context — e.g. the task you're working on, the nature of local changes, or "baseline (clean)" for a pre-change run. This makes `list` and `compare` output self-explanatory.

**Workflow for code changes (API, prompt, chunking, post-processing):**

```bash
# 1. Stash your changes to get a clean baseline
git stash push -- explainshell/extraction/llm/

# 2. Run benchmark on the old code
python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50 -d "baseline before <short summary of change>"

# 3. Restore your changes
git stash pop

# 4. Run benchmark on the new code
python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50 -d "<short summary of change>"

# 5. Compare the two most recent reports
python tools/llm_bench.py compare
```

**Usage:**

```bash
# Run on the default corpus (auto-saves to report directory)
python tools/llm_bench.py run --model openai/gpt-5-mini

# Run with batch API
python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50

# Run on specific files
python tools/llm_bench.py run --model openai/gpt-5-mini path/to/file.1.gz

# Save to a specific path instead of the report directory
python tools/llm_bench.py run --model openai/gpt-5-mini -o report.json tests/regression/manpages/

# Compare the two most recent reports
python tools/llm_bench.py compare

# Compare two specific reports
python tools/llm_bench.py compare report1.json report2.json

# List all reports
python tools/llm_bench.py list
```

## Code Style

- Use Python type annotations on all new code (function signatures, return types, and non-obvious variables). Do not retroactively annotate existing code unless you are already modifying it.

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

# Run all tests (unit + e2e + parsing regression)
make tests-all

# Run DB integrity checks
make db-check

# Run web server locally
make serve

# Generate Ubuntu manpage archive (requires Go)
make ubuntu-archive UBUNTU_RELEASE=questing

# Generate Arch Linux manpage archive (requires manned.org dump)
make arch-archive

# Process a man page into the database
python -m explainshell.manager --mode source /path/to/manpage.1.gz
```

## Project Structure

- `explainshell/` - Main package
  - `manager.py` - CLI entry point for man page processing (`python -m explainshell.manager`)
  - `matcher.py` - Core logic: walks bash AST and matches tokens to help text
  - `models.py` - Core domain types (Option, ParsedManpage, RawManpage) as Pydantic/dataclass models
  - `store.py` - SQLite storage layer
  - `errors.py` - Exception hierarchy (ProgramDoesNotExist, DuplicateManpage, InvalidSourcePath, ExtractionError, SkippedExtraction, LowConfidenceError)
  - `diff.py` - Man page comparison and diff formatting
  - `tree_parser.py` - Mandoc -T tree output parser with confidence assessment
  - `roff_parser.py` - Roff macro parser (man/mdoc dialects)
  - `roff_utils.py` - Roff source detection (dashless opts, nested cmd)
  - `manpage.py` - Man page reading and HTML conversion
  - `help_constants.py` - Shell constant definitions for help text
  - `util.py` - Shared utilities (group_continuous, Peekable, name_section)
  - `config.py` - Configuration (DB_PATH, HOST_IP, DEBUG, MANPAGE_URLS)
  - `extraction/` - Man page option extraction pipeline
    - `__init__.py` - Public API: `make_extractor(mode)` factory
    - `types.py` - Shared types (ExtractionResult, ExtractionStats, BatchResult, ExtractorConfig, Extractor protocol)
    - `source.py` - Roff-based extractor (via `roff_parser.py`)
    - `mandoc.py` - Mandoc-based extractor (via `tree_parser.py`)
    - `hybrid.py` - Hybrid extractor: mandoc with LLM fallback
    - `runner.py` - Execution orchestration (sequential, parallel, batch)
    - `common.py` - Shared metadata assembly for all extractors
    - `postprocess.py` - Extractor-agnostic option post-processing
    - `llm/` - LLM-based extraction subpackage
      - `extractor.py` - LLM extractor orchestration
      - `prompt.py` - Prompt construction
      - `response.py` - LLM response parsing
      - `text.py` - Man page text preparation and chunking
      - `providers/` - LLM provider implementations (OpenAI, Gemini, LiteLLM fallback)
  - `web/views.py` - Flask routes with URL-based distro/release routing
- `tools/` - Standalone scripts
  - `db_check.py` - DB integrity checker (malformed paths, shadowed duplicates, orphans)
  - `llm_bench.py` - LLM extractor benchmark tool (run/compare metrics reports)
  - `fetch_manned.py` - Fetch man pages from manned.org weekly dump
  - `mandoc-with-markdown` - Custom mandoc binary with markdown output support
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
- `--mode source` - Parses roff macros directly via `roff_parser.py` + `extraction/source.py`
- `--mode mandoc` - Uses mandoc -T tree parser via `extraction/mandoc.py`
- `--mode llm:<provider/model>` - Sends man page text to an LLM (e.g., `llm:openai/gpt-5-mini`). Supports OpenAI, Gemini, and LiteLLM (fallback) providers.
- `--mode hybrid:<provider/model>` - Tries mandoc first, falls back to LLM on low confidence

Manager key flags: `--overwrite`, `--dry-run`, `--diff [db|A..B]`, `--debug-dir`, `--drop`, `-j/--jobs <int>` (parallel extraction, default 1)

Flag validation: `--mode` and `--dry-run` cannot be combined with `--diff A..B`; `--drop`, `--overwrite`, and `--diff` are mutually exclusive with each other and with `--dry-run`.

### Data Model (models.py, store.py)

SQLite with two tables:
- **manpage** - source (unique basename), name, synopsis, options (JSON), aliases, flags
- **mapping** - command name → manpage id lookup (many-to-one, with score for preference)

Key classes (Pydantic models in models.py):
- `Option` - text, short/long flag lists, has_argument, positional, nested_cmd
- `ParsedManpage` - container with options/positionals properties and `find_option(flag)` lookup

### Command Matching (matcher.py)

Uses bashlex AST visitor pattern:
- `Matcher` inherits from `bashlex.ast.nodevisitor`
- `visitcommand()` - looks up man page, handles multi-command (e.g., `git commit`)
- `visitword()` - matches tokens to options (exact match, then fuzzy split for combined short flags like `-abc`)
- Produces `MatchResult(start, end, text, match)` where start/end are character positions in the original string

### E2E Tests

Hermetic setup: uses a dedicated `tests/e2e/e2e.db` and random port selection. Server is started fresh per run (`reuseExistingServer: false`).

### Deployment

The app is deployed to [Fly.io](https://fly.io) with two machines for availability. The SQLite database is stored on persistent Fly volumes mounted at `/data`.

**Deploy code changes:**

```bash
fly deploy
```

**Update the database:**

1. `make upload-live-db` (uploads `explainshell.db` to the GitHub release and waits for the CDN to update)
2. Bump `DB_VERSION` in `fly.toml`
3. `fly deploy`

On startup, `start.sh` compares `DB_VERSION` against a marker file on the volume. If they differ, it downloads the DB from `DB_URL` and updates the marker. Normal restarts skip the download.
