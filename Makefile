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

parsing-regression-llm:
	@test -f tests/regression/regression-llm.db || (echo "LLM regression DB not found. Build it with: make parsing-update-llm"; exit 1)
	python -m pytest tests/regression/test_parsing_regression.py -v --extractor llm $(if $(MODEL),--model $(MODEL),)

parsing-update:
	rm -f tests/regression/regression.db
	python -m explainshell.manager --mode source --db tests/regression/regression.db tests/regression/manpages/

parsing-update-llm:
	rm -f tests/regression/regression-llm.db
	python -m explainshell.manager --mode llm:$(or $(MODEL),openai/gpt-5-mini) --batch 50 --db tests/regression/regression-llm.db \
		$$(python -c "from tests.regression.test_parsing_regression import _LLM_CORPUS, _REGRESSION_DIR; import glob, os; print(' '.join(p for p in sorted(glob.glob(os.path.join(_REGRESSION_DIR, '**', '*.gz'), recursive=True)) if os.path.basename(p) in _LLM_CORPUS))")

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

BENCH_REPORT := tests/regression/llm-bench.json
BENCH_BASELINE := tests/regression/llm-bench-baseline.json
BENCH_MODEL := openai/gpt-5-mini
BENCH_DIR := tests/regression/manpages/ubuntu/25.10
# 10 files, 17 chunks — covers tiny→huge, 1→6 chunks, dashless_opts,
# nested_cmd, aliases, and section 1+8 mix.
BENCH_CORPUS := \
	$(BENCH_DIR)/1/echo.1.gz \
	$(BENCH_DIR)/1/docker.1.gz \
	$(BENCH_DIR)/1/sed.1.gz \
	$(BENCH_DIR)/1/xargs.1.gz \
	$(BENCH_DIR)/1/ps.1.gz \
	$(BENCH_DIR)/1/grep.1.gz \
	$(BENCH_DIR)/1/tar.1.gz \
	$(BENCH_DIR)/1/ssh.1.gz \
	$(BENCH_DIR)/1/find.1.gz \
	$(BENCH_DIR)/1/curl.1.gz

# Run LLM benchmark on the bench corpus.
llm-bench:
	python tools/llm_bench.py run --model $(or $(MODEL),$(BENCH_MODEL)) --batch 50 \
		-o $(BENCH_REPORT) $(BENCH_CORPUS)

# Save the current report as the baseline.
llm-bench-baseline:
	@test -f $(BENCH_REPORT) || (echo "No report found. Run 'make llm-bench' first."; exit 1)
	cp $(BENCH_REPORT) $(BENCH_BASELINE)
	@echo "Baseline saved to $(BENCH_BASELINE)"

# Compare current report against baseline.
llm-bench-compare:
	@test -f $(BENCH_BASELINE) || (echo "No baseline found. Run 'make llm-bench-baseline' first."; exit 1)
	@test -f $(BENCH_REPORT) || (echo "No report found. Run 'make llm-bench' first."; exit 1)
	python tools/llm_bench.py compare $(BENCH_BASELINE) $(BENCH_REPORT)

MANNED_DATA_DIR := ignore/manned

arch-archive:
	@test ! -d manpages/arch || (echo "manpages/arch already exists, delete it first"; exit 1)
	@test -d $(MANNED_DATA_DIR) || (echo "Manned.org dump not found. Download it first with:"; echo "  python tools/fetch_manned.py download --data-dir $(MANNED_DATA_DIR)"; exit 1)
	python tools/fetch_manned.py --log INFO extract --data-dir $(MANNED_DATA_DIR) \
		--distro arch --sections 1,8 --output-dir manpages

.PHONY: tests e2e e2e-db e2e-update test-llm tests-all lint serve parsing-regression parsing-regression-llm parsing-update parsing-update-llm db-check ubuntu-archive arch-archive llm-bench llm-bench-baseline llm-bench-compare
