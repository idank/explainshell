from explainshell import util

def convertparagraphs(manpage):
    for p in manpage.paragraphs:
        p.text = p.text.decode('utf-8')
    return manpage

def others(mps):
    mps.sort(key=lambda mp: mp.section)
    return [{'link' : '%s/%s' % (mp.section, util.namesection(mp.source[:-3])[0]),
             'name' : mp.namesection} for mp in mps]
