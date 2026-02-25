import os

_curr_dir = os.path.dirname(os.path.dirname(__file__))

# host to pass into Flask's app.run.
HOST_IP = os.getenv("HOST_IP", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(_curr_dir, "explainshell.db"))
DEBUG = True

MANPAGES_DIR = os.path.join(_curr_dir, "manpages")

# Mapping from source-path prefix to external URL template.
# Templates may use {section} and {name} placeholders.
MANPAGE_URLS = {
    "ubuntu/25.10": "https://manpages.ubuntu.com/manpages/plucky/en/man{section}/{name}.{section}.html",
}


def source_from_path(gz_path):
    """Return source identifier: relative path from MANPAGES_DIR if possible, else basename."""
    try:
        rel = os.path.relpath(gz_path, MANPAGES_DIR)
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    return os.path.basename(gz_path)
