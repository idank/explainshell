import logging
import itertools
import os
import re
import urllib
import markupsafe

import markdown as markdown_lib
from flask import Blueprint, current_app, render_template, request, redirect

import bashlex.errors

from explainshell import matcher, errors, util, config
from explainshell.web import get_cached_distros, helpers

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)

_md = markdown_lib.Markdown()


def _is_known_distro(name):
    """Return True if *name* matches a distro in the cached distros list."""
    for distro, _release in get_cached_distros():
        if distro == name:
            return True
    return False


def _explain_prefix(distro, release):
    """Return the URL prefix for explain routes, with or without distro."""
    if distro and release:
        return f"/explain/{distro}/{release}"
    return "/explain"


def _get_distro_release(url_distro=None, url_release=None):
    """Resolve distro/release: URL params > cookies > default."""
    if url_distro and url_release:
        return url_distro, url_release
    distro = request.cookies.get("distro")
    release = request.cookies.get("release")
    if distro and release:
        return distro, release
    pairs = list(get_cached_distros())
    if pairs:
        pairs.sort(key=lambda dr: (dr[0] != "ubuntu", dr[1]), reverse=True)
        return pairs[0]
    return None, None


def _get_current_url_distro_release():
    """Parse the current request path for distro/release segments.

    Returns (distro, release) if the path starts with /explain/<distro>/<release>/...,
    otherwise (None, None).
    """
    path = request.path
    if not path.startswith("/explain/"):
        return None, None
    rest = path[len("/explain/") :]
    parts = rest.split("/")
    if len(parts) >= 2 and _is_known_distro(parts[0]):
        return parts[0], parts[1]
    return None, None


def render_markdown(text: str) -> str:
    """Convert markdown text to HTML. Falls through to escaped text on error."""
    try:
        _md.reset()
        # Escape bare <word> placeholders (e.g. <newbase>, <file>) so the
        # markdown library doesn't swallow them as HTML tags.  Leave
        # blockquote '>' at line starts and already-escaped &lt;/&gt; alone.
        text = re.sub(r"<([^>]+)>", r"&lt;\1&gt;", text)
        return _md.convert(text)
    except Exception:
        return markupsafe.escape(text)


@bp.app_context_processor
def inject_distros():
    url_distro, url_release = _get_current_url_distro_release()
    active_distro, active_release = _get_distro_release(url_distro, url_release)
    return {
        "available_distros": get_cached_distros(),
        "explain_prefix": _explain_prefix(url_distro, url_release),
        "active_distro": active_distro,
        "active_release": active_release,
    }


@bp.route("/")
def index():
    return render_template("index.html")


debug_bp = Blueprint("manpage", __name__)


@debug_bp.route("/manpage/<distro>/")
def manpage_releases(distro):
    """List available releases for a distro."""
    releases = sorted(release for d, release in get_cached_distros() if d == distro)
    return render_template(
        "manpage_releases.html",
        distro=distro,
        releases=releases,
    )


@debug_bp.route("/manpage/<distro>/<release>/")
def manpage_sections(distro, release):
    """List available sections for a distro/release."""
    sections = current_app.store.list_sections(distro, release)
    return render_template(
        "manpage_sections.html",
        distro=distro,
        release=release,
        sections=sections,
    )


@debug_bp.route("/manpage/<distro>/<release>/<section>/")
def manpage_list(distro, release, section):
    """List available manpages within a section."""
    prefix = f"{distro}/{release}/{section}/"
    sources = current_app.store.list_manpages(prefix)
    entries = []
    for source in sorted(sources):
        # source is e.g. "ubuntu/25.10/1/cd.1posix.gz"; strip the
        # distro/release prefix and .gz suffix to form the URL tail.
        tail = source[len(f"{distro}/{release}/") : -3]  # "1/cd.1posix"
        basename = os.path.basename(source)[:-3]
        name, sec = util.name_section(basename)
        entries.append(
            {
                "name": name,
                "section": sec,
                "url": f"/manpage/{distro}/{release}/{urllib.parse.quote(tail)}",
            }
        )

    return render_template(
        "manpage_list.html",
        distro=distro,
        release=release,
        section=section,
        entries=entries,
    )


@debug_bp.route("/manpage/<distro>/<release>/<path:rest>")
def manpage(distro, release, rest):
    """Serve the raw manpage source text via exact lookup.

    *rest* is everything after distro/release, e.g. "1/cd.1posix",
    mapping to source key "ubuntu/25.10/1/cd.1posix.gz".
    """
    source = f"{distro}/{release}/{rest}.gz"
    raw = current_app.store.get_raw_manpage(source)
    if raw is None:
        return render_template(
            "errors/missingmanpage.html",
            title="missing man page",
            e=errors.ProgramDoesNotExist(rest),
        )

    is_markdown = "markdown" in raw.generator
    name, sec = util.name_section(os.path.basename(source)[:-3])
    return render_template(
        "manpage.html",
        program=f"{name}({sec})",
        source_text=raw.source_text,
        source_html=render_markdown(raw.source_text) if is_markdown else None,
        is_markdown=is_markdown,
    )


@bp.route("/explain", defaults={"path": ""})
@bp.route("/explain/<path:path>")
def explain_router(path):
    """Unified router that handles all /explain/* URLs.

    Path disambiguation:
    - With ?cmd=: 0 segments = no distro, 2 segments = distro/release
    - Without ?cmd=: 1=program, 2=section/program, 3=distro/release/program,
      4=distro/release/section/program
    """
    parts = [p for p in path.split("/") if p] if path else []
    has_cmd = "cmd" in request.args

    url_distro = None
    url_release = None
    section = None
    program = None

    if has_cmd:
        if len(parts) == 0:
            pass  # no distro
        elif len(parts) == 2 and _is_known_distro(parts[0]):
            url_distro, url_release = parts[0], parts[1]
        else:
            # invalid path with ?cmd
            return redirect("/")
    else:
        if len(parts) == 0:
            return redirect("/")
        elif len(parts) == 1:
            program = parts[0]
        elif len(parts) == 2:
            if _is_known_distro(parts[0]):
                # /explain/ubuntu/25.10 with no ?cmd → redirect to index
                return redirect("/")
            section, program = parts[0], parts[1]
        elif len(parts) == 3 and _is_known_distro(parts[0]):
            url_distro, url_release, program = parts[0], parts[1], parts[2]
        elif len(parts) == 4 and _is_known_distro(parts[0]):
            url_distro, url_release, section, program = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
            )
        else:
            return redirect("/")

    if has_cmd:
        return _handle_explain_cmd(url_distro, url_release)
    else:
        return _handle_explain_program(section, program, url_distro, url_release)


def _handle_explain_cmd(url_distro, url_release):
    if not request.args.get("cmd", "").strip():
        return redirect("/")
    command = request.args["cmd"].strip()
    command = command[:1000]
    if "\n" in command:
        return render_template(
            "errors/error.html", title="parsing error!", message="no newlines please"
        )

    distro, release = _get_distro_release(url_distro, url_release)
    prefix = _explain_prefix(url_distro, url_release)
    try:
        matches, helptext, debug_info = explain_cmd(
            command,
            current_app.store,
            distro=distro,
            release=release,
            explain_prefix=prefix,
        )
        helptext = [(render_markdown(text), id_) for text, id_ in helptext]
        return render_template(
            "explain.html",
            matches=matches,
            helptext=helptext,
            getargs=command,
            debug_info=debug_info,
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


def _handle_explain_program(section, program, url_distro, url_release):
    logger.info(
        "/explain section=%r program=%r distro=%r release=%r",
        section,
        program,
        url_distro,
        url_release,
    )

    distro, release = _get_distro_release(url_distro, url_release)
    if section is not None:
        program = f"{program}.{section}"

    try:
        mp, suggestions, raw_mp, debug_info = explain_program(
            program, current_app.store, distro=distro, release=release
        )
        return render_template(
            "options.html",
            mp=mp,
            suggestions=suggestions,
            raw_mp=raw_mp,
            debug_info=debug_info,
        )
    except errors.ProgramDoesNotExist as e:
        return render_template(
            "errors/missingmanpage.html", title="missing man page", e=e
        )


def manpage_url(source):
    """Resolve a manpage source path to an external URL, or None."""
    basename = os.path.basename(source)
    name_with_section = basename[:-3]  # remove .gz
    name, section = name_with_section.rsplit(".", 1)

    best_match = None
    best_len = 0
    for prefix, template in config.MANPAGE_URLS.items():
        if source.startswith(prefix + "/") and len(prefix) > best_len:
            best_match = template
            best_len = len(prefix)

    if best_match:
        return best_match.format(section=section, name=name)
    return None


def explain_program(program, store, distro=None, release=None):
    mps = store.find_man_page(program, distro=distro, release=release)
    raw_mp = mps.pop(0)
    program = raw_mp.name_section

    synopsis = raw_mp.synopsis
    if not synopsis:
        synopsis = None

    url = manpage_url(raw_mp.source)

    mp = {
        "source": os.path.basename(raw_mp.source)[:-3],
        "section": raw_mp.section,
        "program": program,
        "synopsis": synopsis,
        "options": [render_markdown(o.text) for o in raw_mp.options],
        "url": url,
    }

    debug_info = {}
    if config.DEBUG:
        for i, o in enumerate(raw_mp.options):
            debug_info[f"option-{i}"] = {
                "kind": "option",
                "short": o.short,
                "long": o.long,
                "has_argument": o.has_argument,
                "positional": o.positional,
                "nested_cmd": o.nested_cmd,
                "meta": o.meta,
            }

    suggestions = []
    for other_mp in mps:
        d = {
            "text": other_mp.name_section,
            "link": f"{other_mp.section}/{other_mp.name}",
        }
        suggestions.append(d)
    logger.info("suggestions: %s", suggestions)
    return mp, suggestions, raw_mp, debug_info


def _make_match(start, end, match, cmd_class, help_class):
    return {
        "match": match,
        "start": start,
        "end": end,
        "spaces": "",
        "commandclass": cmd_class,
        "helpclass": help_class,
    }


def explain_cmd(command, store, distro=None, release=None, explain_prefix="/explain"):
    matcher_ = matcher.Matcher(command, store, distro=distro, release=release)
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
        format_match(d, m, expansions, explain_prefix=explain_prefix)

        ln.append(d)
    matches.append(ln)

    logger.debug(f"processing {len(cmd_groups)} cmd_group results ...")

    for cmd_group in cmd_groups:
        ln = []
        for m in cmd_group.results:
            cmd_class = cmd_group.name
            help_class = f"help-{len(text_ids)}"

            text = str(m.text)

            if len(text.replace("None", "")) > 0:
                help_class = text_ids.setdefault(text, help_class)
            else:
                cmd_class += " unknown"
                help_class = ""
            if help_class:
                id_start_pos.setdefault(help_class, m.start)

            d = _make_match(m.start, m.end, m.match, cmd_class, help_class)
            format_match(d, m, expansions, explain_prefix=explain_prefix)

            ln.append(d)

        d = ln[0]
        d["commandclass"] += " simplecommandstart"
        if cmd_group.manpage:
            d["name"] = cmd_group.manpage.name
            d["section"] = cmd_group.manpage.section
            if "." not in d["match"]:
                d["match"] = f"{d['match']}({d['section']})"
            d["suggestions"] = cmd_group.suggestions
            d["source"] = cmd_group.manpage.name
            d["url"] = manpage_url(cmd_group.manpage.source)
        matches.append(ln)

    matches = list(itertools.chain.from_iterable(matches))
    helpers.suggestions(matches, command)

    matches.sort(key=lambda d: d["start"])

    it = util.Peekable(iter(matches))
    while it.has_next():
        m = next(it)
        spaces = 0
        if it.has_next():
            spaces = it.peek()["start"] - m["end"]
        m["spaces"] = " " * spaces

    helptext = sorted(text_ids.items(), key=lambda kv: id_start_pos[kv[1]])

    debug_info = {}
    if config.DEBUG:
        for group in groups:
            for m in group.results:
                if m.debug_info and m.text in text_ids:
                    help_class = text_ids[m.text]
                    debug_info.setdefault(help_class, m.debug_info)

    return matches, helptext, debug_info


def format_match(d, m, expansions, explain_prefix="/explain"):
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
                content = markupsafe.Markup(_substitution_markup(s, explain_prefix))
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


def _substitution_markup(cmd, explain_prefix="/explain"):
    """
    >>> _substitution_markup('foo')
    '<a href="/explain?cmd=foo" title="Zoom in to nested command">foo</a>'
    >>> _substitution_markup('cat <&3')
    '<a href="/explain?cmd=cat+%3C%263" title="Zoom in to nested command">cat <&3</a>'
    """
    encoded = urllib.parse.urlencode({"cmd": cmd})
    return (
        '<a href="{prefix}?{query}" title="Zoom in to nested command">{cmd}</a>'
    ).format(prefix=explain_prefix, cmd=cmd, query=encoded)
