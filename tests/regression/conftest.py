def pytest_addoption(parser):
    parser.addoption(
        "--extractor",
        default="source",
        choices=("source", "mandoc", "llm"),
        help="Which extractor to test: 'source' (roff), 'mandoc' (tree parser), or 'llm' (LLM extractor)",
    )
    parser.addoption(
        "--model",
        default="openai/gpt-5-mini",
        help="LLM model to use when --extractor=llm (default: openai/gpt-5-mini)",
    )


def pytest_collection_modifyitems(config, items):
    """When --extractor=llm, deselect manpages not in the LLM corpus subset."""
    if config.getoption("--extractor") != "llm":
        return

    import os

    from tests.regression.test_parsing_regression import _LLM_CORPUS

    selected, deselected = [], []
    for item in items:
        gz_path = item.callspec.params.get("gz_path", "")
        if os.path.basename(gz_path) in _LLM_CORPUS:
            selected.append(item)
        else:
            deselected.append(item)

    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)
