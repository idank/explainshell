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
	python -m pytest tests/regression/test_parsing_regression.py -v

parsing-update:
	rm -f tests/regression/regression.db
	python -m explainshell.manager --mode source --db tests/regression/regression.db tests/regression/manpages/

tests-all: lint tests e2e parsing-regression

lint:
	ruff check explainshell tests tools

format:
	ruff format explainshell tests tools

serve:
	python runserver.py

db-check:
	python tools/db_check.py --db $(or $(DB_PATH),explainshell.db)

.PHONY: tests e2e e2e-update test-llm tests-all lint serve parsing-regression parsing-update db-check
