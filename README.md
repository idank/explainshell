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

> [!IMPORTANT]  
>
> explainshell is actively maintained in terms of keeping it healthy and functional -- issues are addressed, and the core remains stable.
> 
> However, please note that the **manpages are outdated**. The previous system for generating them was unsustainable, and they haven’t been updated in some time. There are currently **no active plans** to revise this mechanism.
> 
> If you're relying on manpages, be aware that they may not reflect the latest behavior. Contributions in this area are welcome but would require rethinking the documentation pipeline.

Right now explainshell.com contains the entire [archive of Ubuntu](https://manpages.ubuntu.com/). It's not
possible to directly add a missing man page to the live site (it might be in the future).

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
