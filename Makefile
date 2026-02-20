tests:
	pytest --doctest-modules tests/test-*.py explainshell/

lint:
	@command -v ruff >/dev/null 2>&1 || (echo "ruff is required. Install with: pip install ruff"; exit 1)
	ruff check explainshell tests tools

serve:
	docker compose up --build

.PHONY: tests lint serve
