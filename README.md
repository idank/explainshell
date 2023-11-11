# [explainshell.com](http://www.explainshell.com) - match command-line arguments to their help text

explainshell is a tool (with a web interface) capable of parsing man pages, extracting options and
explaining a given command-line by matching each argument to the relevant help text in the man page.

## How?

explainshell is built from the following components:

1. man page reader which converts a given man page from raw format to html (manpage.py)
2. classifier which goes through every paragraph in the man page and classifies
   it as contains options or not (algo/classifier.py)
3. an options extractor that scans classified paragraphs and looks for options (options.py)
4. a storage backend that saves processed man pages to mongodb (store.py)
5. a matcher that walks the command's AST (parsed by [bashlex](https://github.com/idank/bashlex)) and contextually matches each node
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

## Missing man pages

Right now explainshell.com contains the entire [archive of Ubuntu](http://manpages.ubuntu.com/). It's not
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
# open http://localhost:5000 to view the ui
```

### Processing a man page

Use the manager to parse and save a gzipped man page in raw format:

```ShellSession
$ docker-compose exec -T web bash -c "PYTHONPATH=. python explainshell/manager.py --log info /usr/share/man/man1/echo.1.gz"
INFO:explainshell.store:creating store, db = 'explainshell_tests', host = 'mongodb://localhost'
INFO:explainshell.algo.classifier:train on 994 instances
INFO:explainshell.manager:handling manpage echo (from /tmp/es/manpages/1/echo.1.gz)
INFO:explainshell.store:looking up manpage in mapping with src 'echo'
INFO:explainshell.manpage:executing '/tmp/es/tools/w3mman2html.cgi local=%2Ftmp%2Fes%2Fmanpages%2F1%2Fecho.1.gz'
INFO:explainshell.algo.classifier:classified <paragraph 3, DESCRIPTION: '-n     do not output the trailing newlin'> (0.991381) as an option paragraph
INFO:explainshell.algo.classifier:classified <paragraph 4, DESCRIPTION: '-e     enable interpretation of backslash escape'> (0.996904) as an option paragraph
INFO:explainshell.algo.classifier:classified <paragraph 5, DESCRIPTION: '-E     disable interpretation of backslash escapes (default'> (0.998640) as an option paragraph
INFO:explainshell.algo.classifier:classified <paragraph 6, DESCRIPTION: '--help display this help and exi'> (0.999215) as an option paragraph
INFO:explainshell.algo.classifier:classified <paragraph 7, DESCRIPTION: '--version'> (0.999993) as an option paragraph
INFO:explainshell.store:inserting mapping (alias) echo -> echo (52207a1fa9b52e42fb59df36) with score 10
successfully added echo
```

Note that if you've setup using the docker instructions above, echo will already be in the database.
