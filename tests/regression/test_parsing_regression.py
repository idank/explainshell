"""Parsing regression tests.

Re-parses manpage .gz files with the current source extractor and compares
against what is stored in the DB.  If the parser changes, the test catches
divergences so the user can review and decide whether to update the DB.

Run:  make parsing-regression
Update DB:  make parsing-update

LLM extractor mode (separate baseline):
Run:  make parsing-regression-llm
Update DB:  make parsing-update-llm
"""

import glob
import os

import pytest

from explainshell import errors, store
from explainshell.manager import batch_extract_files, compare_manpages, run_extractor

_REGRESSION_DIR = os.path.join(os.path.dirname(__file__), "manpages")
_REGRESSION_DB = os.path.join(os.path.dirname(__file__), "regression.db")
_REGRESSION_LLM_DB = os.path.join(os.path.dirname(__file__), "regression-llm.db")

_gz_files = sorted(
    glob.glob(os.path.join(_REGRESSION_DIR, "**", "*.gz"), recursive=True)
)

# Subset of manpages for LLM regression testing (to limit API costs).
# Covers: small/medium/large, single/multi-chunk, dashless_opts, nested_cmd,
# aliases, and has_subcommands.
_LLM_CORPUS = {
    "sed.1.gz",  # 15 opts, 1 chunk – small happy path
    "grep.1.gz",  # 47 opts, 1 chunk – medium, aliases
    "docker.1.gz",  # 10 opts, 1 chunk – few options, nested_cmd
    "ps.1.gz",  # 58 opts, 1 chunk – dashless_opts
    "ssh.1.gz",  # 52 opts, 1 chunk – nested_cmd + has_subcommands
    "tar.1.gz",  # 155 opts, 1 chunk – dashless_opts, near chunk boundary
    "find.1.gz",  # 85 opts, 2 chunks – multi-chunk
    "curl.1.gz",  # 269 opts, 6 chunks – heavy multi-chunk, dedup
}


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
def db_store(request):
    extractor = request.config.getoption("--extractor")
    db_path = _REGRESSION_LLM_DB if extractor == "llm" else _REGRESSION_DB
    return store.Store(db_path)


@pytest.fixture(scope="session")
def llm_results(request):
    """Batch-extract all LLM corpus files once per session."""
    if request.config.getoption("--extractor") != "llm":
        return None
    model = request.config.getoption("--model")
    gz_paths = [p for p in _gz_files if os.path.basename(p) in _LLM_CORPUS]
    return batch_extract_files(gz_paths, model)


@pytest.mark.parametrize(
    "gz_path", _gz_files, ids=[os.path.basename(p) for p in _gz_files]
)
def test_parsing_matches_db(gz_path, db_store, llm_results, request):
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
    if extractor == "llm":
        result = llm_results.get(gz_path)
        if result is None:
            pytest.skip(f"Batch extraction returned no result for {source}")
        fresh_mp, _raw = result
    else:
        fresh_mp, _raw = run_extractor(extractor, gz_path)
        if fresh_mp is None:
            pytest.skip(f"Extraction returned None for {source}")

    # has_subcommands is computed post-extraction by update_subcommand_mappings(),
    # not by the parser, so exclude it from comparison.
    diffs = compare_manpages(
        stored_mp,
        fresh_mp,
        skip_fields=("has_subcommands", "extractor", "extraction_meta"),
    )
    assert not diffs, f"Parsing regression for {basename}:\n{_format_diffs(diffs)}"
