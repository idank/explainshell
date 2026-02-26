def pytest_addoption(parser):
    parser.addoption(
        "--extractor", default="source", choices=("source", "mandoc"),
        help="Which extractor to test: 'source' (roff) or 'mandoc' (tree parser)",
    )
