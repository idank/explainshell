## [explainshell.com](http://www.explainshell.com) - match command-line arguments to their help text

explainshell is a tool (with a web interface) capable of parsing man pages, extracting options and
explain a given command-line by matching each argument to the relevant help text in the man page.

### How?

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

#### [TODO](https://raw.github.com/idank/explainshell/master/TODO) file

This is still under development. Some commands may not parse perfectly.


#### Missing man pages

Right now explainshell.com contains the entire [archive of Ubuntu](http://manpages.ubuntu.com/). It's not possible to directly add a missing man page to the live site (it might be in the future). Instead, submit a link [here](https://github.com/idank/explainshell/issues/1)
and I'll add it.

- - -

## Running on Docker

The simplest way to run this locally is using Docker and `docker-compose`.

### Build docker web and db containers

A Docker compose file has been provided to simplify getting started.

#### Build containers

```bash
docker-compose build
```

#### Start containers
Note: this will automatically import the classifiers

```bash
docker-compose up
```

#### Importing man pages

An import script has been provided that will import man pages located in `manpages`, which is shared into the container.

```ShellSession
$ docker exec explainshell_web /opt/webapp/import.sh --help
usage /opt/webapp/import.sh DISTRO

DISTRO: _test centos7 centos8 opensuse15 ubuntu18.04 ubuntu20.04
```

Importing CentOS 7 man pages

```bash
docker exec explainshell_web /opt/webapp/import.sh centos7
```

#### Open browser at port 5000

[http://localhost:5000/](http://localhost:5000/)

### Restore test db to run tests

```bash
docker exec explainshell_web mongorestore --host db -d explainshell_tests dump/explainshell
```

```bash
docker exec explainshell_web make tests
```

```ShellSession
$ docker exec explainshell_web make tests
nosetests --with-doctest tests/ explainshell/
................................................................................
----------------------------------------------------------------------
Ran 80 tests in 2.843s

OK
```

- - -

## Running explainshell locally

Explainshell is still being ported to Python 3. Suggest using Docker Compose to run locally.

To setup a working environment that lets you run the web interface locally, you'll need to:

```ShellSession
$ pip install -r requirements.txt

# load classifier data, needs a mongodb
$ mongorestore dump/explainshell && mongorestore -d explainshell_tests dump/explainshell
$ make tests
..............................................................................
----------------------------------------------------------------------
Ran 80 tests in 3.847s

OK
```

### Processing a man page

Use the manager to parse and save a gzipped man page in raw format:

```ShellSession
$ PYTHONPATH=. python explainshell/manager.py --log info manpages/1/echo.1.gz
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

### Start up a local web server:

```ShellSession
$ make serve
python runserver.py
 * Running on http://127.0.0.1:5000/
 * Restarting with reloader
```