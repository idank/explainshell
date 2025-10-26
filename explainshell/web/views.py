import logging, itertools, urllib
import markupsafe

from flask import render_template, request, redirect

import bashlex.errors

from explainshell import matcher, errors, util, store, config
from explainshell.web import app, helpers

logger = logging.getLogger(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/explain")
def explain():
    if "cmd" not in request.args or not request.args["cmd"].strip():
        return redirect("/")
    command = request.args["cmd"].strip()
    command = command[:1000]  # trim commands longer than 1000 characters
    if "\n" in command:
        return render_template(
            "errors/error.html", title="parsing error!", message="no newlines please"
        )

    s = store.Store("explainshell", config.MONGO_URI)
    try:
        matches, helptext = explain_cmd(command, s)
        return render_template(
            "explain.html", matches=matches, helptext=helptext, getargs=command
        )

    except errors.ProgramDoesNotExist as error_msg:
        return render_template(
            "errors/missingmanpage.html", title="missing man page", e=error_msg
        )
    except bashlex.errors.ParsingError as error_msg:
        logger.warning("%r parsing error: %s", command, error_msg.message)
        return render_template(
            "errors/parsingerror.html", title="parsing error!", e=error_msg
        )
    except NotImplementedError as error_msg:
        logger.warning("not implemented error trying to explain %r", command)
        msg = (
            f"the parser doesn't support {error_msg.args[0]} constructs in the command you tried. you may "
            f"<a href='https://github.com/idank/explainshell/issues'>report a "
            f"bug</a> to have this added, if one doesn't already exist."
        )

        return render_template("errors/error.html", title="error!", message=msg)
    except Exception as error_msg:
        logger.error(error_msg)
        logger.error("uncaught exception trying to explain %r", command, exc_info=True)
        msg = "something went wrong... this was logged and will be checked"
        return render_template("errors/error.html", title="error!", message=msg)


@app.route("/explain/<program>", defaults={"section": None})
@app.route("/explain/<section>/<program>")
def explain_old(section, program):
    logger.info("/explain section=%r program=%r", section, program)

    s = store.Store("explainshell", config.MONGO_URI)
    if section is not None:
        program = f"{program}.{section}"

    # keep links to old urls alive
    if "args" in request.args:
        args = request.args["args"]
        command = f"{program} {args}"
        return redirect(f"/explain?cmd={urllib.parse.quote_plus(command)}", 301)
    else:
        try:
            mp, suggestions = explain_program(program, s)
            return render_template("options.html", mp=mp, suggestions=suggestions)
        except errors.ProgramDoesNotExist as e:
            return render_template(
                "errors/missingmanpage.html", title="missing man page", e=e
            )


def explain_program(program, store):
    mps = store.find_man_page(program)
    mp = mps.pop(0)
    program = mp.name_section

    synopsis = mp.synopsis
    if synopsis:
        synopsis = synopsis.decode("utf-8")

    mp = {
        "source": mp.source[:-3],
        "section": mp.section,
        "program": program,
        "synopsis": synopsis,
        "options": [o.text.decode("utf-8") for o in mp.options],
    }

    suggestions = []
    for other_mp in mps:
        d = {
            "text": other_mp.name_section,
            "link": f"{other_mp.section}/{other_mp.name}",
        }
        suggestions.append(d)
    logger.info("suggestions: %s", suggestions)
    return mp, suggestions


def _make_match(start, end, match, cmd_class, help_class):
    return {
        "match": match,
        "start": start,
        "end": end,
        "spaces": "",
        "commandclass": cmd_class,
        "helpclass": help_class,
    }


def explain_cmd(command, store):
    matcher_ = matcher.Matcher(command, store)
    groups = matcher_.match()
    expansions = matcher_.expansions

    shell_group = groups[0]
    cmd_groups = groups[1:]
    matches = []

    # save a mapping between the help text to its assigned id,
    # we're going to reuse ids that have the same text
    text_ids = {}

    # remember where each assigned id has started in the source,
    # we're going to use it later on to sort the help text by start
    # position
    id_start_pos = {}

    logger.debug(f"processing {len(shell_group.results)} shell_group results ...")

    ln = []
    for m in shell_group.results:
        cmd_class = shell_group.name
        help_class = f"help-{len(text_ids)}"

        text = str(m.text)
        if isinstance(m.text, bytes):
            text = m.text.decode("utf-8")
        if len(text.replace("None", "")) > 0:
            help_class = text_ids.setdefault(text, help_class)
        else:
            # unknowns in the shell group are possible when our parser left
            # an unparsed remainder, see matcher._mark_unparsed_unknown
            cmd_class += " unknown"
            help_class = ""
        if help_class:
            id_start_pos.setdefault(help_class, m.start)

        d = _make_match(m.start, m.end, m.match, cmd_class, help_class)
        format_match(d, m, expansions)

        ln.append(d)
    matches.append(ln)

    logger.debug(f"processing {len(cmd_groups)} cmd_group results ...")

    for cmd_group in cmd_groups:
        ln = []
        for m in cmd_group.results:
            cmd_class = cmd_group.name
            help_class = f"help-{len(text_ids)}"

            text = str(m.text)
            if isinstance(m.text, bytes):
                text = m.text.decode("utf-8")

            if len(text.replace("None", "")) > 0:
                help_class = text_ids.setdefault(text, help_class)
            else:
                cmd_class += " unknown"
                help_class = ""
            if help_class:
                id_start_pos.setdefault(help_class, m.start)

            d = _make_match(m.start, m.end, m.match, cmd_class, help_class)
            format_match(d, m, expansions)

            ln.append(d)

        d = ln[0]
        d["commandclass"] += " simplecommandstart"
        if cmd_group.manpage:
            d["name"] = cmd_group.manpage.name
            d["section"] = cmd_group.manpage.section
            if "." not in d["match"]:
                d["match"] = f"{d['match']}({d['section']})"
            d["suggestions"] = cmd_group.suggestions
            d["source"] = cmd_group.manpage.source[:-5]
        matches.append(ln)

    matches = list(itertools.chain.from_iterable(matches))
    helpers.suggestions(matches, command)

    # _check_overlaps(matcher_.s, matches)
    matches.sort(key=lambda d: d["start"])

    it = util.Peekable(iter(matches))
    while it.has_next():
        m = it.next()
        spaces = 0
        if it.has_next():
            spaces = it.peek()["start"] - m["end"]
        m["spaces"] = " " * spaces

    helptext = sorted(text_ids.items(), key=lambda kv: id_start_pos[kv[1]])

    return matches, helptext


def format_match(d, m, expansions):
    """populate the match field in d by escaping m.match and generating
    links to any command/process substitutions"""

    # save us some work later: do any expansions overlap
    # the current match?
    has_subs_in_match = False

    for start, end, kind in expansions:
        if m.start <= start and end <= m.end:
            has_subs_in_match = True
            break

    # if not, just escape the current match
    if not has_subs_in_match:
        d["match"] = markupsafe.escape(m.match)
        return

    # used in es.js
    d["commandclass"] += " hasexpansion"

    # go over the expansions, wrapping them with a link; leave everything else
    # untouched
    expanded_match = ""
    i = 0
    for start, end, kind in expansions:
        if start >= m.end:
            break
        rel_start = start - m.start
        rel_end = end - m.start

        if i < rel_start:
            for j in range(i, rel_start):
                if m.match[j].isspace():
                    expanded_match += markupsafe.Markup("&nbsp;")
                else:
                    expanded_match += markupsafe.escape(m.match[j])
            i = rel_start + 1
        if m.start <= start and end <= m.end:
            s = m.match[rel_start:rel_end]

            if kind == "substitution":
                content = markupsafe.Markup(_substitution_markup(s))
            else:
                content = s

            expanded_match += markupsafe.Markup(
                '<span class="expansion-{0}">{1}</span>'
            ).format(kind, content)
            i = rel_end

    if i < len(m.match):
        expanded_match += markupsafe.escape(m.match[i:])

    assert expanded_match
    d["match"] = expanded_match


def _substitution_markup(cmd):
    """
    >>> _substitutionmarkup('foo')
    '<a href="/explain?cmd=foo" title="Zoom in to nested command">foo</a>'
    >>> _substitutionmarkup('cat <&3')
    '<a href="/explain?cmd=cat+%3C%263" title="Zoom in to nested command">cat <&3</a>'
    """
    encoded = urllib.parse.urlencode({"cmd": cmd})
    return (
        '<a href="/explain?{query}" title="Zoom in to nested command">{cmd}' "</a>"
    ).format(cmd=cmd, query=encoded)


def _check_overlaps(s, matches):
    explained = [None] * len(s)
    for d in matches:
        for i in range(d["start"], d["end"]):
            if explained[i]:
                raise RuntimeError(
                    f"explained overlap for group {d} at {i} with {explained[i]}"
                )
            explained[i] = d
