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
.PHONY: help test test-unit test-socket test-integration test-fast coverage \
        fixtures-argv fixtures-probe verify-argv clean package-arch

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## ── tests ────────────────────────────────────────────────────────────────────

test: ## Run the whole suite (unit + integration + socket)
	$(PYTHON) -m pytest $(PYTEST)

test-unit: ## Run only the fast unit suites (no ffmpeg, no database)
	$(PYTHON) -m pytest tests/unit $(PYTEST)

test-socket: ## Run the live-socket suite (boots an embedded postgres + real server)
	$(PYTHON) -m pytest tests/socket $(PYTEST)

test-integration: ## Run the real-ffmpeg integration suites
	$(PYTHON) -m pytest tests/integration $(PYTEST)

test-fast: ## Everything except the socket suite: no postgres, no live server
	$(PYTHON) -m pytest -m "not socket" $(PYTEST)

coverage: ## Coverage report (the socket suite included)
	$(PYTHON) -m coverage run -m pytest $(PYTEST)
	$(PYTHON) -m coverage report -m

## ── fixtures ─────────────────────────────────────────────────────────────────
#
# Goldens are regenerated from real code, never hand-edited. `--check` is the
# gate: it re-derives every golden in a temp directory and diffs, so an
# unintended argv change fails instead of being silently re-blessed.

fixtures-argv: ## Re-capture the ffmpeg argv goldens into tests/fixtures/argv/
	$(PYTHON) scripts/capture_argv_fixtures.py

verify-argv: ## Fail if any ffmpeg argv golden is stale (does not rewrite them)
	$(PYTHON) scripts/capture_argv_fixtures.py --check

fixtures-probe: ## Re-capture the ffprobe JSON fixtures into tests/fixtures/probe/
	$(PYTHON) scripts/capture_probe_fixtures.py

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
