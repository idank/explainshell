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

Setup a working environment that lets you run the web interface locally using docker:

```ShellSession

# download db dump
$ curl -L -o /tmp/dump.gz https://github.com/idank/explainshell/releases/download/db-dump/dump.gz

# Clone Repository
$ git clone https://github.com/idank/explainshell.git

# start containers, load man pages from dump
$ docker-compose build
$ docker-compose up

$ docker-compose exec -T db mongorestore --archive --gzip < /tmp/dump.gz

# run tests
$ docker-compose exec -T web make tests
..SSSSSSSSS.....................................................................
----------------------------------------------------------------------
Ran 80 tests in 0.041s

OK (SKIP=9)
# open http://localhost:5001 to view the ui
```

### Processing a man page

Use the manager to parse and save a gzipped man page in raw format:

```ShellSession
$ python -m explainshell.manager --mode source /usr/share/man/man1/echo.1.gz
```

Note that if you've setup using the docker instructions above, echo will already be in the database.
