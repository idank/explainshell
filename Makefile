tests:
	pytest --doctest-modules tests/ explainshell/ --ignore=tests/e2e --ignore=tests/regression

e2e:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	npx playwright test

e2e-update:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	npx playwright test --update-snapshots

test-llm:
	RUN_LLM_TESTS=1 pytest tests/test_llm_extractor.py::TestRealLlm -v

parsing-regression:
	pytest tests/regression/test_parsing_regression.py -v

parsing-update:
	python -m explainshell.manager --mode source --overwrite tests/regression/manpages/

tests-all: tests e2e parsing-regression

lint:
	@command -v ruff >/dev/null 2>&1 || (echo "ruff is required. Install with: pip install ruff"; exit 1)
	ruff check explainshell tests tools

serve:
	python runserver.py

.PHONY: tests e2e e2e-update test-llm tests-all lint serve parsing-regression parsing-update
