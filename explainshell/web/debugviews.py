import logging

from flask import render_template, request, abort, redirect, url_for, json

from explainshell import manager, config, store
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

def _convertvalue(value):
    if isinstance(value, list):
        return [s.strip() for s in value]
    elif value.lower() == 'true':
        return True
    elif value:
        return value.strip()
    return False

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
            expectsarg = _convertvalue(d['expectsarg'])
            nestedcommand = _convertvalue(d['nestedcommand'])
            if isinstance(nestedcommand, str):
                nestedcommand = [nestedcommand]
            elif nestedcommand is True:
                logger.error('nestedcommand %r must be a string or list', nestedcommand)
                abort(503)
            argument = d['argument']
            if not argument:
                argument = None
            p = store.paragraph(idx, text, section, d['is_option'])
            if d['is_option'] and (short or long or argument):
                p = store.option(p, short, long, expectsarg, argument, nestedcommand)
            mparagraphs.append(p)

        if request.form.get('nestedcommand', '').lower() == 'true':
            m.nestedcommand = True
        else:
            m.nestedcommand = False
        m = mngr.edit(m, mparagraphs)
        if m:
            return redirect(url_for('explain', cmd=m.name))
        else:
            abort(503)
    else:
        helpers.convertparagraphs(m)
        for p in m.paragraphs:
            if isinstance(p, store.option):
                if isinstance(p.expectsarg, list):
                    p.expectsarg = ', '.join(p.expectsarg)
                if isinstance(p.nestedcommand, list):
                    p.nestedcommand = ', '.join(p.nestedcommand)

        return render_template('tagger.html', m=m)
