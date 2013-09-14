from explainshell import util

def convertparagraphs(manpage):
    for p in manpage.paragraphs:
        p.text = p.text.decode('utf-8')
    return manpage

def others(matches, command):
    '''enrich command matches with links to other man pages with the
    same name'''
    for m in matches:
        if 'name' in m and 'others' in m:
            before = command[:m['start']]
            after = command[m['end']:]
            newothers = []
            for othermp in sorted(m['others'], key=lambda mp: mp.section):
                mid = '%s.%s' % (othermp.name, othermp.section)
                newothers.append({'cmd' : ''.join([before, mid, after]),
                                  'text' : othermp.namesection})
            m['others'] = newothers
