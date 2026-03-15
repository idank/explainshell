import os

# host to pass into Flask's app.run.
HOST_IP = os.getenv("HOST_IP", "")
DB_PATH = os.getenv("DB_PATH")
DEBUG = os.getenv("DEBUG", "true").lower() not in ("0", "false", "no")

# Mapping from source-path prefix to external URL template.
# Templates may use {section} and {name} placeholders.
MANPAGE_URLS = {
    "ubuntu/25.10": "https://manpages.ubuntu.com/manpages/questing/en/man{section}/{name}.{section}.html",
    "ubuntu/12.04": "https://manpages.ubuntu.com/manpages/precise/en/man{section}/{name}.{section}.html",
}


def parse_distro_release(source):
    """Extract (distro, release) from a source path.

    All source paths follow the ``distro/release/section/file.gz`` format:
      "ubuntu/25.10/1/ps.1.gz" -> ("ubuntu", "25.10")
    """
    parts = source.split("/")
    return parts[0], parts[1]


def source_from_path(gz_path):
    """Return the ``distro/release/section/name.section.gz`` source identifier.

    Extracts the last four path components, which by convention are
    ``distro/release/section/file.gz``.
    """
    parts = os.path.normpath(gz_path).split(os.sep)
    return "/".join(parts[-4:])
