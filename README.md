# [explainshell.com](http://www.explainshell.com) - match command-line arguments to their help text

explainshell is a tool (with a web interface) capable of parsing man pages, extracting options and
explaining a given command-line by matching each argument to the relevant help text in the man page.

## How?

explainshell is built from the following components:

1. man page reader which converts a given man page from raw format to html (manpage.py)
2. an options extractor that parses roff macros or uses an LLM to extract options (source_extractor.py, llm_extractor.py)
3. a storage backend that saves processed man pages to sqlite (store.py)
4. a matcher that walks the command's AST (parsed by [bashlex](https://github.com/idank/bashlex)) and contextually matches each node
   to the relevant help text (matcher.py)

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

## Manpages

explainshell.com contains manpages from the [Ubuntu archive](https://manpages.ubuntu.com/). The manpage archive is generated using a Go pipeline in the `manpages/ubuntu-manpages-operator` submodule, which fetches Ubuntu packages, extracts manpages, and converts them to markdown.

To generate the archive locally:

```bash
$ git submodule update --init --recursive
$ make ubuntu-archive RELEASES=questing
```

This outputs markdown and gzipped manpages under `manpages/ubuntu-manpages-operator/output/`.

## Running explainshell locally

```bash
# Clone repository
$ git clone https://github.com/idank/explainshell.git
$ cd explainshell

# Set up Python virtualenv
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt

# Run the web server (requires explainshell.db in the repo root)
$ make serve
# open http://localhost:5000
```

### Processing a man page

Use the manager to parse and save a gzipped man page in raw format:

```bash
$ python -m explainshell.manager --mode source /usr/share/man/man1/echo.1.gz
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

### Deployment

The app is deployed to [Fly.io](https://fly.io) with two machines for availability. The SQLite database is stored on persistent Fly volumes mounted at `/data`.

**Deploy code changes:**

```bash
$ fly deploy
```

**Update the database:**

```bash
# Upload to each machine
$ fly machines list
$ fly ssh sftp shell -s <machine-id>
# put explainshell.db /data/explainshell.db

# Restart to pick up the new DB
$ fly machines restart <machine-id>
```
