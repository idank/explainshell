tests:
	pytest --doctest-modules tests/ explainshell/

serve:
	docker-compose up --build

.PHONY: tests
