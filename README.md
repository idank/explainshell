# [explainshell.com](http://www.explainshell.com) - match command-line arguments to their help text

[![CI](https://github.com/idank/explainshell/actions/workflows/ci.yml/badge.svg)](https://github.com/idank/explainshell/actions/workflows/ci.yml)
[![Deploy](https://img.shields.io/github/actions/workflow/status/idank/explainshell/do-deploy.yml?label=deploy&logo=digitalocean&logoColor=white)](https://github.com/idank/explainshell/actions/workflows/do-deploy.yml)

explainshell is a tool (with a web interface) capable of parsing manpages, extracting options and
explaining a given command-line by matching each argument to the relevant help text in the manpage.

## How?

explainshell is built from the following components:

1. manpage reader which extracts metadata (name, synopsis, aliases) from a given manpage (manpage.py)
2. an options extractor that parses roff macros or uses an LLM to extract options (extraction/)
3. a storage backend that saves processed manpages to sqlite (store.py)
4. a matcher that walks the command's AST (parsed by [bashlex](https://github.com/idank/bashlex)) and contextually
   matches each node to the relevant help text (matcher.py)

When querying explainshell, it:

1. parses the query into an AST
2. visits interesting nodes in the AST, such as:
   - command nodes - these nodes represent a simple command
   - shell related nodes - these nodes represent something the shell
     interprets such as '|', '&&'
3. for every command node we check if we know how to explain the current program,
   and then go through the rest of the tokens, trying to match each one to the
   list of known options
4. returns a list of matches that are rendered with Flask

## Running explainshell locally

```bash
# Clone repository
$ git clone https://github.com/idank/explainshell.git
$ cd explainshell

# Set up Python virtualenv
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements-dev.txt

# Download the live db
$ make download-latest-db

# Or parse a manpage
$ python -m explainshell.manager extract --mode llm:codex/gpt-5.2/medium manpages/ubuntu/26.04/1/tar.1.gz

# Run the web server
$ make serve
# open http://localhost:5000
```

## Storage

Processed manpages live in a single SQLite database (`explainshell.db`) with three tables:

- **manpages**: zlib-compressed manpage source text (typically markdown produced by `mandoc -T markdown`). Keyed by a `source` path in the format `distro/release/section/name.section.gz` (e.g. `ubuntu/26.04/1/tar.1.gz`).
- **parsed_manpages**: extracted options, synopsis, aliases, and behavioral flags for each manpage. Options are stored as a JSON list.
- **mappings**: maps command names to `parsed_manpages` rows (many-to-one, with a score for preference). A single manpage can have multiple mappings - one per alias and one per sub-command form (e.g. `git commit` maps to the `git-commit` manpage).

The `source` path is the primary key across both `manpages` and `parsed_manpages`, and doubles as a namespace: queries can be scoped to a specific distro/release by filtering on the path prefix.

The db for the live service is saved in a Github release.

## Manpage archives

explainshell.com sources manpages from known archives (currently the Ubuntu archive and manned.org). All manpage sources
(gz files) are committed to explainshell-manpages (a git submodule of this repo). It's not necessary to clone this repo
to run explainshell locally.

To generate the archive locally:

```bash
$ git submodule update --init --recursive
$ make ubuntu-archive UBUNTU_RELEASE=resolute
$ make arch-archive # Arch only has a 'latest' archive
```

This outputs gzipped manpages under `manpages/<distro>/<release>/`.

### Processing manpages

The manager CLI requires a database path for most commands. Set it via the `DB_PATH` environment variable or pass `--db <path>` on the command line. Commands that don't need a database (e.g. `extract --dry-run`, `diff extractors`) work without it.

Use the manager to extract options from gzipped manpages and save them to the database:

```bash
# Calls out to 'codex exec' to extract the manpage and writes the result to test.db.
$ python -m explainshell.manager --db test.db extract --mode llm:codex/gpt-5.2/medium manpages/ubuntu/26.04/1/tar.1.gz

# Or set DB_PATH:
$ export DB_PATH=$(pwd)/test.db

# Can also use an API key (see .env.example).
$ python -m explainshell.manager extract --mode llm:openai/gpt-5-mini manpages/ubuntu/26.04/1/find.1.gz
$ python -m explainshell.manager extract --mode llm:openai/gpt-5-mini --batch 50 manpages/ubuntu/26.04/
```

The `--mode` flag selects the extraction strategy:

- `llm:<provider/model>`: sends the manpage text (converted to markdown via `mandoc -T markdown`) to an LLM for extraction. The LLM returns line ranges into the source text, not generated descriptions, so hallucinations are structurally impossible - the actual help text is always sliced from the original manpage. Example: `--mode llm:openai/gpt-5-mini`.

Other `extract` flags: `--overwrite` (re-process existing entries), `--filter-db <spec>` (with `--overwrite`, only re-extract rows whose stored extractor matches `<spec>`; same syntax as `--mode`), `--dry-run` (extract without writing to DB), `-j <N>` (parallel workers), `--batch <N>` (provider batch API for LLM modes, including `gemini/`, `openai/`, and `azure/`).

To compare extraction results, use the `diff` subcommand:

```bash
# Diff against the database
$ python -m explainshell.manager diff db --mode llm:openai/gpt-5-mini manpages/ubuntu/26.04/1/tar.1.gz

# Compare two extractors head-to-head
$ python -m explainshell.manager diff extractors llm:openai/gpt-5-mini..llm:openai/gpt-5 manpages/ubuntu/26.04/1/tar.1.gz
```

### Querying the database

```bash
# Show aggregate stats
$ python -m explainshell.manager show stats

# Look up a command
$ python -m explainshell.manager show manpage tar

# List available distros
$ python -m explainshell.manager show distros

# Run integrity checks
$ python -m explainshell.manager db-check
```

## Tests

```bash
$ make tests-all          # lint + unit tests + e2e
```

### LLM evaluation

The LLM eval (`tests/evals/llm/llm_eval.py`) runs the LLM extractor on the corpus listed in `tests/evals/llm/corpus.txt` and produces a `summary.json` plus per-page artifacts (`markdown/`, `prompts/`, `responses/`). Runs are auto-saved with timestamps to `tests/evals/llm/runs/<timestamp>-<label>/` and include git metadata.

The idea is to run it before making changes that affect the LLM extractor pipeline (producing a baseline), and then again
with the changes. Some variance between runs is expected due to LLM indeterminism.

```bash
# Run on the default corpus, parallelizing realtime calls
$ python tests/evals/llm/llm_eval.py run --label baseline --model openai/gpt-5-mini --jobs 10

# Compare two run directories (oldest first)
$ python tests/evals/llm/llm_eval.py compare tests/evals/llm/runs/<baseline-run> tests/evals/llm/runs/<current-run>

# List all saved runs
$ python tests/evals/llm/llm_eval.py list
```

Use `--batch <size>` instead of `--jobs` to route through the provider's batch API — cheaper but minutes-to-hours of queue latency, so it pays off only on much larger corpora than the default 12-page one.

### Markdown render eval

A review-oriented harness for `mandoc -T markdown` changes lives in `tests/evals/render/`. It renders a vendored manpage corpus with two mandoc binaries, compares structural metrics, and builds a screenshot diff report with a draggable expected/actual slider. It is intentionally not part of `make tests-all` - run it manually when changing the markdown rendering path. See [tests/evals/render/README.md](tests/evals/render/README.md) for usage.
