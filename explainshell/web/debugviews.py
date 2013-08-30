import logging, os, glob

from flask import render_template, request, send_from_directory, abort, redirect, url_for, json

from explainshell import manager, config, manpage, store
from explainshell.web import app, helpers

logger = logging.getLogger(__name__)

@app.route('/debug')
def debug():
    s = store.store('explainshell', config.MONGO_URI)
    d = {'manpages' : []}
    for mp in s:
        synopsis = ''
        if mp.synopsis:
            synopsis = mp.synopsis[:20]
        dd = {'name' : mp.name, 'synopsis' : synopsis}
        l = []
        for o in mp.options:
            l.append(str(o))
        dd['options'] = ', '.join(l)
        d['manpages'].append(dd)
    d['manpages'].sort(key=lambda d: d['name'].lower())
    return render_template('debug.html', d=d)

@app.route('/debug/tag/<source>', methods=['GET', 'POST'])
def tag(source):
    mngr = manager.manager(config.MONGO_URI, 'explainshell', [], False, False)
    s = mngr.store
    m = s.findmanpage(source)[0]
    assert m

    if 'paragraphs' in request.form:
        paragraphs = json.loads(request.form['paragraphs'])
        mparagraphs = []
        for d in paragraphs:
            idx = d['idx']
            text = d['text']
            section = d['section']
            short = [s.strip() for s in d['short']]
            long = [s.strip() for s in d['long']]
            if isinstance(d['expectsarg'], list):
                expectsarg = [s.strip() for s in d['expectsarg']]
            elif d['expectsarg'].lower() == 'true':
                expectsarg = True
            elif d['expectsarg']:
                expectsarg = d['expectsarg']
            else:
                expectsarg = False
            argument = d['argument']
            if not argument:
                argument = None
            p = store.paragraph(idx, text, section, d['is_option'])
            if d['is_option'] and (short or long or argument):
                p = store.option(p, short, long, expectsarg, argument)
            mparagraphs.append(p)

        m = mngr.edit(m, mparagraphs)
        if m:
            return redirect(url_for('explain', program=m.name))
        else:
            abort(503)
    else:
        helpers.convertparagraphs(m)
        for p in m.paragraphs:
            if isinstance(p, store.option) and isinstance(p.expectsarg, list):
                p.expectsarg = ', '.join(p.expectsarg)

        return render_template('tagger.html', m=m)
