import re

import cmarkgfm
import markupsafe


def render_markdown(text: str) -> str:
    """Convert markdown text to HTML. Falls through to escaped text on error."""
    try:
        # Escape bare <word> placeholders (e.g. <newbase>, <file>) so the
        # markdown library doesn't swallow them as HTML tags.
        text = re.sub(r"<([^>]+)>", r"&lt;\1&gt;", text)
        return cmarkgfm.markdown_to_html(text)
    except Exception:
        return markupsafe.escape(text)
