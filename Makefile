tests:
	pytest --doctest-modules tests/ explainshell/ --ignore=tests/e2e --ignore=tests/regression

E2E_MANPAGES_25 := tests/e2e/manpages/ubuntu/25.10/1
E2E_MANPAGES_24 := tests/e2e/manpages/ubuntu/24.04/1
E2E_DB := tests/e2e/e2e.db

e2e-db:
	rm -f $(E2E_DB)
	python -m explainshell.manager --mode source --db $(E2E_DB) $(E2E_MANPAGES_25)/tar.1.gz $(E2E_MANPAGES_25)/echo.1.gz $(E2E_MANPAGES_25)/grep.1.gz $(E2E_MANPAGES_24)/tar.1.gz
	python -m explainshell.manager --mode llm:openai/gpt-5.2 --db $(E2E_DB) $(E2E_MANPAGES_25)/git-rebase.1.gz

e2e:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	@test -f $(E2E_DB) || (echo "e2e database not found. Build it with: make e2e-db"; exit 1)
	npx playwright test

e2e-update:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	@test -f $(E2E_DB) || (echo "e2e database not found. Build it with: make e2e-db"; exit 1)
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
	ruff format --check explainshell tests tools
	npx biome check

format:
	ruff format explainshell tests tools
	npx biome check --fix

serve:
	DB_PATH=$(or $(DB_PATH),explainshell.db) python runserver.py

db-check:
	python tools/db_check.py --db $(or $(DB_PATH),explainshell.db)

UBUNTU_ARCHIVE_DIR := manpages/ubuntu-manpages-operator
UBUNTU_ARCHIVE_OUTPUT := $(UBUNTU_ARCHIVE_DIR)/output
UBUNTU_RELEASE ?= 25.10

ubuntu-archive:
	@test ! -d manpages/ubuntu/$(UBUNTU_RELEASE) || (echo "manpages/ubuntu/$(UBUNTU_RELEASE) already exists, delete it first"; exit 1)
	cd $(UBUNTU_ARCHIVE_DIR) && go build -o ingest ./cmd/ingest
	MANPAGES_PUBLIC_HTML_DIR=$(UBUNTU_ARCHIVE_OUTPUT) \
	MANPAGES_RELEASES=$(UBUNTU_RELEASE) \
	MANPAGES_GZ_ONLY=true \
		$(UBUNTU_ARCHIVE_DIR)/ingest
	mkdir -p manpages/ubuntu
	cp -r $(UBUNTU_ARCHIVE_OUTPUT)/manpages.gz/* manpages/ubuntu/
	find manpages/ubuntu -mindepth 2 -maxdepth 2 ! -name man1 ! -name man8 -exec rm -rf {} +
	find manpages/ubuntu -mindepth 2 -maxdepth 2 -type d -name 'man*' | while read d; do mv "$$d" "$$(dirname "$$d")/$$(echo "$$(basename "$$d")" | sed 's/^man//')"; done
	find manpages/ubuntu -mindepth 3 -maxdepth 3 -type d -exec rm -rf {} +

MANNED_DATA_DIR := ignore/manned

arch-archive:
	@test ! -d manpages/arch || (echo "manpages/arch already exists, delete it first"; exit 1)
	@test -d $(MANNED_DATA_DIR) || (echo "Manned.org dump not found. Download it first with:"; echo "  python tools/fetch_manned.py download --data-dir $(MANNED_DATA_DIR)"; exit 1)
	python tools/fetch_manned.py --log INFO extract --data-dir $(MANNED_DATA_DIR) \
		--distro arch --sections 1,8 --output-dir manpages

.PHONY: tests e2e e2e-db e2e-update test-llm tests-all lint serve parsing-regression parsing-update db-check ubuntu-archive arch-archive
