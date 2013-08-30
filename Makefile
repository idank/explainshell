tests:
	nosetests --with-doctest tests/ explainshell/

serve:
	python runserver.py

.PHONY: tests
