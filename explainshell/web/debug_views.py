import logging

from flask import render_template, request, abort, redirect, url_for, json

from explainshell import manager, config, store
from explainshell.web import app, helpers

logger = logging.getLogger(__name__)


@app.route("/debug")
def debug():
    s = store.Store("explainshell", config.MONGO_URI)
    d = {"manpages": []}
    for mp in s:
        synopsis = ""
        if mp.synopsis:
            synopsis = mp.synopsis[:20]
        dd = {"name": mp.name, "synopsis": synopsis}
        o_list = []
        for o in mp.options:
            o_list.append(str(o))
        dd["options"] = ", ".join(o_list)
        d["manpages"].append(dd)
    d["manpages"].sort(key=lambda d: d["name"].lower())
    return render_template("debug.html", d=d)


def _convert_value(value):
    if isinstance(value, list):
        return [s.strip() for s in value]
    elif value.lower() == "true":
        return True
    elif value:
        return value.strip()
    return False


@app.route("/debug/tag/<source>", methods=["GET", "POST"])
def tag(source):
    mngr = manager.Manager(config.MONGO_URI, "explainshell", [], False, False)
    s = mngr.store
    m = s.find_man_page(source)[0]
    assert m

    if "paragraphs" in request.form:
        paragraphs = json.loads(request.form["paragraphs"])
        m_paragraphs = []
        for d in paragraphs:
            idx = d["idx"]
            text = d["text"]
            section = d["section"]
            short = [s.strip() for s in d["short"]]
            long = [s.strip() for s in d["long"]]
            expects_arg = _convert_value(d["expects_arg"])
            nested_cmd = _convert_value(d["nested_cmd"])
            if isinstance(nested_cmd, str):
                nested_cmd = [nested_cmd]
            elif nested_cmd is True:
                logger.error("nested_cmd %r must be a string or list", nested_cmd)
                abort(503)
            argument = d["argument"]
            if not argument:
                argument = None
            p = store.Paragraph(idx, text, section, d["is_option"])
            if d["is_option"] and (short or long or argument):
                p = store.Option(p, short, long, expects_arg, argument, nested_cmd)
            m_paragraphs.append(p)

        if request.form.get("nested_cmd", "").lower() == "true":
            m.nested_cmd = True
        else:
            m.nested_cmd = False
        m = mngr.edit(m, m_paragraphs)
        if m:
            return redirect(url_for("explain", cmd=m.name))
        else:
            abort(503)
    else:
        helpers.convert_paragraphs(m)
        for p in m.paragraphs:
            if isinstance(p, store.Option):
                if isinstance(p.expects_arg, list):
                    p.expects_arg = ", ".join(p.expects_arg)
                if isinstance(p.nested_cmd, list):
                    p.nested_cmd = ", ".join(p.nested_cmd)

        return render_template("tagger.html", m=m)
