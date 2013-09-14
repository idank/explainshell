import logging, itertools
from flask import render_template, request, redirect

from explainshell import matcher, errors, util, store, config
from explainshell.web import app, helpers

logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/explain')
def explain():
    if 'cmd' not in request.args:
        return redirect('/')
    command = request.args['cmd']
    s = store.store('explainshell', config.MONGO_URI)
    matches, helptext = explaincommand(command, None, s)
    return render_template('explain.html', matches=matches, helptext=helptext, getargs=command)

@app.route('/explain/<program>', defaults={'section' : None})
@app.route('/explain/<section>/<program>')
def explainold(section, program):
    s = store.store('explainshell', config.MONGO_URI)
    try:
        if 'args' in request.args:
            args = request.args['args']
            command = '%s %s' % (program, args)
            matches, helptext = explaincommand(command, section, s)
            return render_template('explain.html', matches=matches, helptext=helptext, getargs=args)
        else:
            logger.info('/explain section=%r program=%r', section, program)
            mp, othersections = explainprogram(program, section, s)
            return render_template('options.html', mp=mp, othersections=othersections)
    except errors.ProgramDoesNotExist, e:
        return render_template('missingmanpage.html', prog=e.args[0])
    except errors.ParsingError, e:
        return render_template('error.html', message='Parsing error: %s' % str(e))

def explainprogram(program, section, store):
    mps = store.findmanpage(program, section)
    mp = mps.pop(0)
    program = mp.namesection

    mp = {'source' : mp.source[:-3],
          'section' : mp.section,
          'program' : program,
          'synopsis' : mp.synopsis,
          'options' : [o.text.decode('utf-8') for o in mp.options]}

    othersections = helpers.others(mps)
    logger.info('others: %s', othersections)
    return mp, othersections

def explaincommand(command, section, store):
    matcher_ = matcher.matcher(command, store, section)
    groups = matcher_.match()
    shellgroup = groups[0]
    commandgroups = groups[1:]
    matches = []

    # save a mapping between the help text to its assigned id,
    # we're going to reuse ids that have the same text
    texttoid = {}

    # remember where each assigned id has started in the source,
    # we're going to use it later on to sort the help text by start
    # position
    idstartpos = {}

    l = []
    for m in shellgroup.results:
        id_ = '%s-%d' % (shellgroup.name, len(l))
        text = m.text
        if text:
            text = text.decode('utf-8')
            id_ = texttoid.setdefault(text, id_)
        else:
            assert False
        idstartpos.setdefault(id_, m.start)
        d = {'match' : m.match, 'unknown' : m.unknown,
             'start' : m.start, 'end' : m.end,
             'id' : id_}
        l.append(d)
    matches.append(l)

    for commandgroup in commandgroups:
        l = []
        for m in commandgroup.results:
            id_ = '%s-%d' % (commandgroup.name, len(l))
            text = m.text
            if text:
                text = text.decode('utf-8')
                id_ = texttoid.setdefault(text, id_)
            else:
                id_ = '%s unknown' % commandgroup.name
            idstartpos.setdefault(id_, m.start)
            d = {'match' : m.match, 'unknown' : m.unknown,
                 'start' : m.start, 'end' : m.end,
                 'id' : id_}
            l.append(d)

        d = l[0]
        if commandgroup.manpage:
            d['name'] = commandgroup.manpage.name
            d['section'] = commandgroup.manpage.section
            if '.' not in d['match']:
                d['match'] = '%s(%s)' % (d['match'], d['section'])
            d['others'] = commandgroup.others
            d['source'] = commandgroup.manpage.source[:-5]
        matches.append(l)

    matches = list(itertools.chain.from_iterable(matches))
    helpers.others(matches, command)
    matches.sort(key=lambda d: d['start'])

    it = util.peekable(iter(matches))
    while it.hasnext():
        m = it.next()
        spaces = 0
        if it.hasnext():
            spaces = it.peek()['start'] - m['end']
        m['spaces'] = ' ' * spaces

    helptext = sorted(texttoid.iteritems(), key=lambda (k, v): idstartpos[v])
    return matches, helptext
