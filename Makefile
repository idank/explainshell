tests:
	pytest --doctest-modules tests/test-*.py explainshell/

tests-all: tests e2e

lint:
	@command -v ruff >/dev/null 2>&1 || (echo "ruff is required. Install with: pip install ruff"; exit 1)
	ruff check explainshell tests tools

serve:
	docker compose up --build

e2e:
	@command -v playwright >/dev/null 2>&1 || (echo "playwright is required. Install with: pip install -r requirements-e2e.txt && playwright install chromium"; exit 1)
	pytest tests/test-e2e.py -v

e2e-update:
	@command -v playwright >/dev/null 2>&1 || (echo "playwright is required. Install with: pip install -r requirements-e2e.txt && playwright install chromium"; exit 1)
	UPDATE_SNAPSHOTS=1 pytest tests/test-e2e.py -v

.PHONY: tests lint serve e2e e2e-update tests-all
