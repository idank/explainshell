# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A web tool that parses man pages and explains command-line arguments by matching each argument to its help text.

## Tech Stack

- Python 3.12, Flask, SQLite, bashlex, LiteLLM
- Linting: ruff
- Testing: pytest (unit + doctests), JS Playwright Test (e2e)
- Dependencies: `requirements.txt` (main), `package.json` (Playwright e2e)

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

# Run e2e tests (requires playwright)
make e2e

# Update e2e snapshots
make e2e-update

# Run LLM integration test (requires API key in .env)
make test-llm

# Run all tests (unit + e2e)
make tests-all

# Run web server locally
make serve

# Process a man page into the database
python -m explainshell.manager --mode source /path/to/manpage.1.gz
```

## Project Structure

- `explainshell/` - Main package
  - `manager.py` - CLI entry point for man page processing (`python -m explainshell.manager`)
  - `matcher.py` - Core logic: walks bash AST and matches tokens to help text
  - `store.py` - SQLite storage layer and data classes (ManPage, Option, Paragraph)
  - `llm_extractor.py` - LLM-based option extraction (via LiteLLM)
  - `source_extractor.py` - Direct roff parsing extractor
  - `roff_parser.py` - Roff macro parser (man/mdoc dialects)
  - `manpage.py` - Man page reading and HTML conversion
  - `web/views.py` - Flask routes
  - `config.py` - Configuration (DB_PATH, HOST_IP, DEBUG)
- `tests/` - Unit tests (`test_*.py`), fixtures
- `tests/e2e/` - Playwright e2e tests and snapshots
- `runserver.py` - Flask app entry point

## Architecture

### Man Page Processing Pipeline

`manager.py` orchestrates: raw .gz → parse → extract options → store in SQLite.

Two extraction modes controlled by `--mode`:
- `--mode source` - Parses roff macros directly via `roff_parser.py` + `source_extractor.py`
- `--mode llm:<model>` - Sends man page text to an LLM via LiteLLM (e.g., `llm:gpt-4o`)

Manager key flags: `--overwrite`, `--dry-run`, `--diff [db|modes]`, `--debug-dir`, `--drop`

### Data Model (store.py)

SQLite with two tables:
- **manpage** - source (unique basename), name, synopsis, paragraphs (JSON), aliases, flags
- **mapping** - command name → manpage id lookup (many-to-one, with score for preference)

Key classes:
- `Paragraph` - text block with idx, text, section, is_option flag
- `Option(Paragraph)` - extends Paragraph with short/long flag lists, expects_arg, argument, nested_cmd
- `ManPage` - container with options/arguments properties and `find_option(flag)` lookup

### Command Matching (matcher.py)

Uses bashlex AST visitor pattern:
- `Matcher` inherits from `bashlex.ast.nodevisitor`
- `visitcommand()` - looks up man page, handles multi-command (e.g., `git commit`)
- `visitword()` - matches tokens to options (exact match, then fuzzy split for combined short flags like `-abc`)
- Produces `MatchResult(start, end, text, match)` where start/end are character positions in the original string

### Test Conventions

- Test files use `test_*.py` naming (underscored)
- Doctests embedded in `util.py`, `manpage.py`
- E2E tests and snapshots live in `tests/e2e/`
- E2E snapshot updates via `make e2e-update`
- **Always run `make tests` after making changes** to verify nothing is broken
- **Always lint the codebase after making changes**
