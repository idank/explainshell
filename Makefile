tests:
	nosetests --exe --with-doctest tests/ explainshell/

serve:
	python runserver.py

.PHONY: tests
