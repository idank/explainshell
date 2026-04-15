tests:
	pytest --doctest-modules tests/ explainshell/ --ignore=tests/e2e --ignore=tests/regression

E2E_MANPAGES_25 := tests/e2e/manpages/ubuntu/25.10/1
E2E_MANPAGES_24 := tests/e2e/manpages/ubuntu/24.04/1
E2E_MANPAGES_ARCH := tests/e2e/manpages/arch/latest/1
E2E_DB := tests/e2e/e2e.db

e2e-db:
	rm -f $(E2E_DB)
	python -m explainshell.manager --db $(E2E_DB) extract --mode source $(E2E_MANPAGES_25)/tar.1.gz $(E2E_MANPAGES_25)/echo.1.gz $(E2E_MANPAGES_25)/grep.1.gz $(E2E_MANPAGES_24)/tar.1.gz $(E2E_MANPAGES_24)/echo.1.gz $(E2E_MANPAGES_24)/grep.1.gz $(E2E_MANPAGES_ARCH)/tar.1.gz
	python -m explainshell.manager --db $(E2E_DB) extract --mode llm:openai/gpt-5.2 $(E2E_MANPAGES_25)/git-rebase.1.gz

e2e:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	@test -f $(E2E_DB) || (echo "e2e database not found. Build it with: make e2e-db"; exit 1)
	npx playwright test

e2e-update:
	@npx playwright --version >/dev/null 2>&1 || (echo "playwright is required. Install with: npm install && npx playwright install chromium"; exit 1)
	@test -f $(E2E_DB) || (echo "e2e database not found. Build it with: make e2e-db"; exit 1)
	npx playwright test --update-snapshots

test-llm:
	RUN_LLM_TESTS=1 pytest tests/extraction/llm/test_extractor.py::test_real_llm_echo_manpage -v

parsing-regression:
	python -m pytest tests/regression/test_parsing_regression.py -v

parsing-update:
	rm -f tests/regression/regression.db
	python -m explainshell.manager --db tests/regression/regression.db extract --mode source tests/regression/manpages/

tests-all: lint tests e2e parsing-regression

tests-quick: lint tests parsing-regression

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
	python -m explainshell.manager --db $(or $(DB_PATH),explainshell.db) db-check

UBUNTU_ARCHIVE_DIR := manpages/ubuntu-manpages-operator
UBUNTU_ARCHIVE_OUTPUT := $(UBUNTU_ARCHIVE_DIR)/output
UBUNTU_RELEASE ?= 26.04

ubuntu-archive:
	cd $(UBUNTU_ARCHIVE_DIR) && go build -o ingest ./cmd/ingest
	MANPAGES_PUBLIC_HTML_DIR=$(UBUNTU_ARCHIVE_OUTPUT) \
	MANPAGES_RELEASES=$(UBUNTU_RELEASE) \
	MANPAGES_GZ_ONLY=true \
		$(UBUNTU_ARCHIVE_DIR)/ingest
	python tools/postprocess_ubuntu_archive.py \
		$(UBUNTU_ARCHIVE_OUTPUT)/manpages.gz/$(UBUNTU_RELEASE) \
		manpages/ubuntu/$(UBUNTU_RELEASE)

MANNED_DATA_DIR := ignore/manned

arch-archive:
	@test ! -d manpages/arch/latest || (echo "manpages/arch/latest already exists, delete it first"; exit 1)
	@test -d $(MANNED_DATA_DIR) || (echo "Manned.org dump not found. Download it first with:"; echo "  python tools/fetch_manned.py download --data-dir $(MANNED_DATA_DIR)"; exit 1)
	python tools/fetch_manned.py --log INFO extract --data-dir $(MANNED_DATA_DIR) \
		--distro arch --sections 1,1p,8 --output-dir manpages

LIVE_DB := explainshell.db

download-latest-db:
	tools/download-latest-db.sh $(LIVE_DB)

upload-live-db:
	tools/upload-live-db.sh $(LIVE_DB)

.PHONY: tests e2e e2e-db e2e-update test-llm tests-all tests-quick lint serve parsing-regression parsing-update db-check ubuntu-archive arch-archive download-latest-db upload-live-db
