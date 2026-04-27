# Project Instructions — explainshell

A web tool that parses man pages and explains command-line arguments by matching each argument to its help text.

## Tech Stack

- Python 3.12, Flask, SQLite, bashlex, OpenAI SDK, Google Gemini SDK, LiteLLM (fallback)
- Linting: ruff (Python), biome (JS)
- Testing: pytest (unit + doctests), JS Playwright Test (e2e)
- Dependencies: `requirements.txt` (main), `package.json` (Playwright e2e)

## Workflow Requirements

**Before finishing any task**, always:

1. Run `make format`
1. Run tests — choose the right suite based on what changed:
   - `make tests-quick` (lint + unit) — use when changes clearly cannot affect what the web app serves (e.g., extraction pipeline, CLI tooling, tests themselves)
   - `make tests-all` (lint + unit + e2e) — use when changes might affect the web serving path (rendering, matching, storage, templates, static assets, config)
   - When in doubt, run `make tests-all`
   - If e2e tests fail due to snapshot diffs, assess whether the diff is expected, and get user confirmation before running `make e2e-update`
1. Update README.md if the change adds/removes/renames CLI commands, env vars, or user-facing features
1. Update AGENTS.md if the change affects structure, convention, workflow, etc.

### LLM Evaluation

Use the LLM eval (`tests/evals/llm/llm_eval.py`) to compare before/after metrics when making changes to the LLM extractor. It runs extraction on the corpus listed in `tests/evals/llm/corpus.txt` (paths into the `manpages/` submodule) and writes `summary.json` plus per-page artifacts under `markdown/`, `prompts/`, and `responses/` to a timestamped directory under `tests/evals/llm/runs/`. Summaries include git metadata, model, label, description, aggregate metrics (extracted/failed files, total options, zero-option pages, multi-chunk pages, token usage), and per-page metrics keyed by repo-relative path.

`run` requires `--label <tag>` (folded into the run dir name) and accepts `-d "..."` for a longer description. Always pass a meaningful label and description inferred from context — e.g. the task you're working on, or "baseline" for a pre-change run — so `list` and `compare` output stay self-explanatory.

**Workflow for code changes (API, prompt, chunking, post-processing):**

```bash
# 1. Stash your changes to get a clean baseline
git stash push -- explainshell/extraction/llm/

# 2. Run on the old code
python tests/evals/llm/llm_eval.py run --label baseline --model openai/gpt-5-mini --batch 50 -d "baseline before <short summary of change>"

# 3. Restore your changes
git stash pop

# 4. Run on the new code
python tests/evals/llm/llm_eval.py run --label change --model openai/gpt-5-mini --batch 50 -d "<short summary of change>"

# 5. Compare the two run directories (oldest first)
python tests/evals/llm/llm_eval.py compare tests/evals/llm/runs/<baseline-run> tests/evals/llm/runs/<change-run>
```

**Usage:**

```bash
# Run on the default corpus
python tests/evals/llm/llm_eval.py run --label smoke --model openai/gpt-5-mini

# Run with batch API
python tests/evals/llm/llm_eval.py run --label smoke --model openai/gpt-5-mini --batch 50

# Run on specific files (overrides --corpus)
python tests/evals/llm/llm_eval.py run --label probe --model openai/gpt-5-mini path/to/file.1.gz

# Compare two run directories
python tests/evals/llm/llm_eval.py compare tests/evals/llm/runs/<baseline-run> tests/evals/llm/runs/<current-run>

# List all saved runs
python tests/evals/llm/llm_eval.py list
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

# Run quick tests (lint + unit, no e2e)
make tests-quick

# Run all tests (lint + unit + e2e)
make tests-all

# Run DB integrity checks
make db-check

# Run web server locally
make serve

# Generate Ubuntu manpage archive (requires Go)
make ubuntu-archive UBUNTU_RELEASE=resolute

# Generate Arch Linux manpage archive (requires manned.org dump)
make arch-archive

# Process a man page into the database
python -m explainshell.manager extract --mode llm:openai/gpt-5-mini /path/to/manpage.1.gz
```

## Project Structure

- `explainshell/` - Main package
  - `manager.py` - CLI entry point for man page processing (`python -m explainshell.manager <command>`)
  - `db_check.py` - Database integrity checks (used by `manager.py db-check`)
  - `matcher.py` - Core logic: walks bash AST and matches tokens to help text
  - `models.py` - Core domain types (Option, ParsedManpage, RawManpage) as Pydantic/dataclass models
  - `store.py` - SQLite storage layer
  - `caching_store.py` - Read-only size-aware cached Store variant for production web serving; when `DEBUG=false`, the Flask app stores one per worker process in `app.extensions`
  - `errors.py` - Exception hierarchy (ProgramDoesNotExist, DuplicateManpage, InvalidSourcePath, ExtractionError, SkippedExtraction, FatalExtractionError)
  - `diff.py` - Man page comparison and diff formatting
  - `roff_parser.py` - Roff macro parser (used by `roff_utils.py` for detection helpers)
  - `roff_utils.py` - Roff source detection (dashless opts, nested cmd)
  - `manpage.py` - Man page reading and HTML conversion
  - `help_constants.py` - Shell constant definitions for help text
  - `util.py` - Shared utilities (group_continuous, Peekable, name_section)
  - `config.py` - Configuration (DB_PATH, HOST_IP, DEBUG, MANPAGE_URLS)
  - `extraction/` - Man page option extraction pipeline
    - `__init__.py` - Public API: `make_extractor(mode)` factory
    - `types.py` - Shared types (ExtractionResult, ExtractionStats, BatchResult, ExtractorConfig, Extractor protocol)
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
  - `fetch_manned.py` - Fetch man pages from manned.org weekly dump
  - `mandoc-md` - Custom mandoc binary with markdown output support
- `tests/` - Unit tests (`test_*.py`), fixtures
- `tests/e2e/` - Playwright e2e tests, snapshots, and dedicated `e2e.db`
- `tests/evals/` - Manual review-oriented evals (not in `make tests-all`)
  - `_common.py` - Shared helpers (corpus reading, summary loading, metric lookup)
  - `llm/` - LLM extractor eval (`llm_eval.py`, `corpus.txt`, `runs/`)
  - `render/` - Mandoc markdown render eval (`render_eval.py`, `corpus.txt`, `runs/`)
- `runserver.py` - Flask app entry point
- `manpages/` - Git submodule ([explainshell-manpages](https://github.com/idank/explainshell-manpages))
  - `ubuntu-manpages-operator/` - Go pipeline that fetches Ubuntu `.deb` packages, extracts manpages, and converts them to markdown

## Architecture

### Man Page Processing Pipeline

`manager.py` orchestrates: raw .gz → parse → extract options → store in SQLite.

The CLI uses subcommands. Most commands require a database path, set via `DB_PATH` env var or `--db <path>`. Commands that don't need a database (e.g. `extract --dry-run`, `diff extractors`) work without it. Main commands:

- `extract --mode <mode> [options] files...` — Extract options from manpages and store in DB
- `diff db --mode <mode> files...` — Diff fresh extraction against the database
- `diff extractors <A..B> files...` — Compare two extractors head-to-head
- `show {manpage,distros,sections,manpages,mappings,stats}` — Query the database
- `db-check` — Run database integrity checks

Extraction modes (passed via `--mode` to `extract` or `diff db`):
- `llm:<provider/model>` - Sends man page text to an LLM (e.g., `llm:openai/gpt-5-mini`, `llm:azure/my-deployment`). Supports Gemini, OpenAI, Azure OpenAI, and LiteLLM (fallback) providers. For `azure/...`, the model suffix is the Azure deployment name and requires `AZURE_OPENAI_API_KEY` plus either `AZURE_OPENAI_BASE_URL` or `AZURE_OPENAI_ENDPOINT`.

Extract flags: `--overwrite`, `--filter-db <spec>` (conditional overwrite; requires `--overwrite`; same syntax as `--mode`), `--dry-run`, `--debug`, `--drop`, `-j/--jobs <int>` (parallel extraction, default 1), `--batch <int>` (provider batch API). All run output (logs, debug artifacts, manifests) goes to `logs/{timestamp}/`.

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

### Web Store Lifecycle

The web app uses `CachingStore` only when `DEBUG=false` (production and e2e). The cached store is created lazily per worker process and stored in `app.extensions`. Local dev (`DEBUG=true`, the default for `make serve`) uses a per-request plain `Store` so DB rebuilds are visible without restarting the server. Tooling such as `explainshell.manager` and `tests/evals/llm/llm_eval.py` should continue using plain `Store`, not `CachingStore`.

### Deployment

The app is deployed to [DigitalOcean App Platform](https://www.digitalocean.com/products/app-platform). The SQLite database is baked into the Docker image at build time (downloaded as `.zst` from the GitHub release, decompressed during `docker build`).

**Production infrastructure:**

- **Domain:** `explainshell.com` → Cloudflare (orange cloud proxy) → DigitalOcean App Platform
- **Cloudflare:** DNS + proxy, SSL mode set to **Full (Strict)**
- **App spec:** `prod/digitalocean/app.yaml` — region, instance size/count, env vars, custom domain. `doctl apps update --spec` does a full replace, so anything configured out-of-band gets wiped on the next deploy; check it in here instead.
- **Container artifacts:** `prod/docker/` (Dockerfile, Caddyfile, start.sh)

**Deploy code changes:**

Deploys are driven by CI: merging to `master` triggers `.github/workflows/do-deploy.yml`, which resolves the newest `db-latest` asset name, renders the spec via `envsubst` with `DB_NAME` and `GIT_SHA`, applies it with `doctl apps update --spec`, then forces a fresh build with `doctl apps create-deployment --force-rebuild --wait`. The force-rebuild step is load-bearing: `deploy_on_push` is off, so without it DO deploys from its cached (stale) branch head instead of the current commit.

**Update the database:**

1. `make upload-live-db` — uploads an `explainshell-{date}.db.zst` asset to the `db-latest` release (skipped if digest matches the current newest).
2. Push to `master` — the deploy pipeline resolves the newest asset name, passes it as the Docker `DB_NAME` build-arg, and the download layer cache-busts to fetch it.
