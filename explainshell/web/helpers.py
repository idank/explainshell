from explainshell import util


def convert_paragraphs(manpage):
    for p in manpage.paragraphs:
        p.text = p.text.decode("utf-8")
    return manpage


def suggestions(matches, command):
    """enrich command matches with links to other man pages with the
    same name"""
    for m in matches:
        if "name" in m and "suggestions" in m:
            before = command[: m["start"]]
            after = command[m["end"]:]
            new_suggestions = []
            for other_mp in sorted(m["suggestions"], key=lambda mp: mp.section):
                mid = f"{other_mp.name}.{other_mp.section}"
                new_suggestions.append(
                    {"cmd": "".join([before, mid, after]), "text": other_mp.name_section}
                )
            m["suggestions"] = new_suggestions
