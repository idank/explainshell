"""Parsing regression tests.

Re-parses manpage .gz files with the current source extractor and compares
against what is stored in the DB.  If the parser changes, the test catches
divergences so the user can review and decide whether to update the DB.

Run:  make parsing-regression
Update DB:  make parsing-update
"""

import glob
import os

import pytest

from explainshell import errors, mandoc_extractor, source_extractor, store
from explainshell.manager import compare_manpages

_REGRESSION_DIR = os.path.join(os.path.dirname(__file__), "manpages")
_REGRESSION_DB = os.path.join(os.path.dirname(__file__), "regression.db")

_gz_files = sorted(
    glob.glob(os.path.join(_REGRESSION_DIR, "**", "*.gz"), recursive=True)
)


def _format_diffs(diffs):
    """Format compare_manpages() output into a readable failure message."""
    lines = []
    for d in diffs:
        dtype = d["type"]
        label = d["label"]
        if dtype == "field":
            _, old_val, new_val = d["details"][0]
            lines.append(f"  {label}:")
            lines.append(f"    - {old_val!r}")
            lines.append(f"    + {new_val!r}")
        elif dtype == "option_changed":
            lines.append(f"  option {label}:")
            for field, old_val, new_val in d["details"]:
                lines.append(f"    {field}:")
                lines.append(f"      - {old_val!r}")
                lines.append(f"      + {new_val!r}")
        elif dtype == "option_added":
            lines.append(f"  + option {label} (added)")
        elif dtype == "option_removed":
            lines.append(f"  - option {label} (removed)")
    return "\n".join(lines)


@pytest.fixture(scope="session")
def db_store():
    return store.Store(_REGRESSION_DB)


@pytest.mark.parametrize(
    "gz_path", _gz_files, ids=[os.path.basename(p) for p in _gz_files]
)
def test_parsing_matches_db(gz_path, db_store, request):
    basename = os.path.basename(gz_path)
    source = os.path.relpath(gz_path, _REGRESSION_DIR)
    extractor = request.config.getoption("--extractor")

    # Look up stored manpage by full source path (distro/release/section/name.gz).
    try:
        results = db_store.find_man_page(source)
        stored_mp = results[0]
    except errors.ProgramDoesNotExist:
        pytest.skip(f"{source} not in DB")

    # Re-parse with selected extractor.
    if extractor == "mandoc":
        fresh_mp, _raw = mandoc_extractor.extract(gz_path)
    else:
        fresh_mp, _raw = source_extractor.extract(gz_path)

    # has_subcommands is computed post-extraction by update_subcommand_mappings(),
    # not by the parser, so exclude it from comparison.
    diffs = compare_manpages(
        stored_mp,
        fresh_mp,
        skip_fields=("has_subcommands", "extractor", "extraction_meta"),
    )
    assert not diffs, f"Parsing regression for {basename}:\n{_format_diffs(diffs)}"
