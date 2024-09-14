tests:
	nosetests --exe --with-doctest tests/ explainshell/

serve:
	docker-compose up --build

.PHONY: tests
