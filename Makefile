tests:
	pytest --doctest-modules tests/ explainshell/ --ignore=tests/e2e

e2e:
	@command -v playwright >/dev/null 2>&1 || (echo "playwright is required. Install with: pip install -r requirements-e2e.txt && playwright install chromium"; exit 1)
	pytest tests/e2e/ -v

e2e-update:
	@command -v playwright >/dev/null 2>&1 || (echo "playwright is required. Install with: pip install -r requirements-e2e.txt && playwright install chromium"; exit 1)
	UPDATE_SNAPSHOTS=1 pytest tests/e2e/ -v

test-llm:
	RUN_LLM_TESTS=1 pytest tests/test_llm_extractor.py::TestRealLlm -v

tests-all: tests e2e

lint:
	@command -v ruff >/dev/null 2>&1 || (echo "ruff is required. Install with: pip install ruff"; exit 1)
	ruff check explainshell tests tools

serve:
	python runserver.py

.PHONY: tests e2e e2e-update test-llm tests-all lint serve
