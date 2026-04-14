# [explainshell.com](http://www.explainshell.com) - match command-line arguments to their help text

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

# Download the live db, or parse a manpage.
$ make download-live-db
$ python -m explainshell.manager extract --mode source manpages/ubuntu/26.04/1/tar.1.gz

# Run the web server
$ make serve
# open http://localhost:5000
```

Runtime env vars for the web app:

- `HOST_IP` - bind address for the local dev server, default `127.0.0.1`
- `DB_PATH` - SQLite database path
- `DEBUG` - enables Flask debug behavior and debug-only web routes/templates
- `LOG_LEVEL` - log level for `explainshell.*` application logs
- `GUNICORN_WORKERS` - Gunicorn worker count for the container entrypoint
- `GUNICORN_THREADS` - Gunicorn thread count per worker
- `GUNICORN_ACCESS_LOG` - enables Gunicorn access logs when set to `1` or `true` (disabled by default)
- `GUNICORN_ACCESS_LOG_FILE` - Gunicorn access log destination when enabled, default `-`
- `GUNICORN_ACCESS_LOG_FORMAT` - Gunicorn access log format string when enabled

## Storage

Processed manpages live in a single SQLite database (`explainshell.db`) with three tables:

- **manpages**: zlib-compressed manpage source text (typically markdown produced by `mandoc -T markdown`). Keyed by a `source` path in the format `distro/release/section/name.section.gz` (e.g. `ubuntu/25.10/1/tar.1.gz`).
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
$ make ubuntu-archive UBUNTU_RELEASE=questing
$ make arch-archive # Arch only has a 'latest' archive
```

This outputs gzipped manpages under `manpages/<distro>/<release>/`.

### Processing manpages

The manager CLI requires a database path for most commands. Set it via the `DB_PATH` environment variable or pass `--db <path>` on the command line. Commands that don't need a database (e.g. `extract --dry-run`, `diff extractors`) work without it.

Use the manager to extract options from gzipped manpages and save them to the database:

```bash
# Uses the source extractor and writes the result to test.db.
$ python -m explainshell.manager --db test.db extract --mode source manpages/ubuntu/26.04/1/tar.1.gz

# Or set DB_PATH:
$ export DB_PATH=$(pwd)/test.db

# LLM extraction requires an API key
$ python -m explainshell.manager extract --mode llm:openai/gpt-5-mini manpages/ubuntu/26.04/1/find.1.gz
$ python -m explainshell.manager extract --mode llm:openai/gpt-5-mini --batch 50 manpages/ubuntu/26.04/
```

The `--mode` flag selects the extraction strategy:

- `source`: parses roff macros directly. Fast, no external dependencies beyond `lexgrog`, but struggles with some manpage formats.
- `llm:<provider/model>`: sends the manpage text (converted to markdown via `mandoc -T markdown`) to an LLM for extraction. More accurate, especially for complex or non-standard manpages. The LLM returns line ranges into the source text, not generated descriptions, so hallucinations are structurally impossible - the actual help text is always sliced from the original manpage. Example: `--mode llm:openai/gpt-5-mini`.

Other `extract` flags: `--overwrite` (re-process existing entries), `--dry-run` (extract without writing to DB), `-j <N>` (parallel workers), `--batch <N>` (provider batch API for LLM modes, including `gemini/`, `openai/`, and `azure/`).

To compare extraction results, use the `diff` subcommand:

```bash
# Diff against the database
$ python -m explainshell.manager diff db --mode source manpages/ubuntu/26.04/1/tar.1.gz

# Compare two extractors head-to-head
$ python -m explainshell.manager diff extractors source..llm:openai/gpt-5-mini manpages/ubuntu/26.04/1/tar.1.gz
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
$ make tests-all          # lint + unit tests + e2e + parsing regression
```

### Parsing regression

The parsing regression suite re-extracts manpages from a fixed corpus and compares against a stored baseline DB. Any difference in options, fields, or option metadata is reported as a failure.

```bash
$ make parsing-regression           # run with the source (roff) extractor
$ make parsing-update               # regenerate the source baseline DB
```

### LLM benchmarking

The benchmark tool (`tools/llm_bench.py`) runs the LLM extractor on a corpus of manpages and produces a JSON metrics report (extracted/failed files, total options, token usage, etc.). Reports are auto-saved with timestamps to `tests/regression/llm-bench/` and include git metadata.

The idea is to run it before making changes that affect the LLM extractor pipeline (producing a baseline), and then again
with the changes. Some variance between runs is expected due to LLM indeterminism.

```bash
# Run on the default 10-file corpus
$ python tools/llm_bench.py run --model openai/gpt-5-mini

# Run with batch API
$ python tools/llm_bench.py run --model openai/gpt-5-mini --batch 50

# Compare the two most recent reports
$ python tools/llm_bench.py compare

# List all saved reports
$ python tools/llm_bench.py list
```
