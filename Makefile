tests:
	pytest --doctest-modules tests/test-*.py explainshell/

serve:
	docker compose up --build

.PHONY: tests
