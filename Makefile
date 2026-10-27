# vergoboy-stream — developer entry points.
#
# Everything runs through `pytest`. The 170 legacy unittest.TestCase tests in
# tests/ are collected by pytest unchanged (it drives unittest natively), so
# there is one runner and one result summary rather than two that can disagree.
# Migrating the legacy tests to pytest style is deliberately NOT done here.
#
# PYTEST is overridable: `make test PYTEST='-k seek -x'`.

VENV   ?= venv
PYTHON ?= $(VENV)/bin/python
PYTEST ?= -q

.DEFAULT_GOAL := help
.PHONY: help check-venv test test-unit test-socket test-integration test-fast coverage \
        fixtures-argv fixtures-probe verify-argv clean package-arch

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# The venv is the one thing make cannot build; uv can. Every test target depends
# on this, so a missing or wrong interpreter says exactly how to fix it instead
# of surfacing as a bare "No such file or directory" that hides a broken
# symlink. conftest separately rejects a venv that cannot import the suite.
check-venv:
	@test -x "$(PYTHON)" || { \
		echo "error: $(PYTHON) is missing or not executable."; \
		echo "  create the project venv with:"; \
		echo "      uv venv --python 3.12 $(VENV)"; \
		echo "      uv pip install --python $(PYTHON) -r requirements-dev.lock.txt"; \
		exit 1; }

## ── tests ────────────────────────────────────────────────────────────────────

test: check-venv ## Run the whole suite (unit + integration + socket)
	$(PYTHON) -m pytest $(PYTEST)

test-unit: check-venv ## Run only the fast unit suites (no database)
	$(PYTHON) -m pytest tests/unit $(PYTEST)

test-socket: check-venv ## Run the live-socket suite (boots an embedded postgres + real server)
	$(PYTHON) -m pytest tests/socket $(PYTEST)

test-integration: check-venv ## Run tests marked "integration" (none today: nothing transcodes)
	$(PYTHON) -m pytest tests/integration $(PYTEST)

test-fast: check-venv ## Unit + socket suites
	$(PYTHON) -m pytest -m "not integration" $(PYTEST)

coverage: check-venv ## Coverage report (the socket suite included)
	$(PYTHON) -m coverage run -m pytest $(PYTEST)
	$(PYTHON) -m coverage report -m

## ── packaging ────────────────────────────────────────────────────────────────
#
# The full option set lives in the script: ./build-arch.sh --help

package-arch: ## Build the Arch/pacman .pkg.tar.zst (clean makepkg run)
	./build-arch.sh

## ── housekeeping ─────────────────────────────────────────────────────────────

clean: ## Remove caches and coverage output (never touches media/ or data/)
	rm -rf .pytest_cache htmlcov
	find . -path ./venv -prune -o -name '__pycache__' -type d -print0 \
		| xargs -0 rm -rf
	rm -f .coverage .coverage.*
