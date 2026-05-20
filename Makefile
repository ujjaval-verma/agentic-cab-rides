# Makefile is the discovery surface for this repo.
# `make help` lists every target. Hooks call into this; no logic duplication.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.SILENT: help status

UV ?= uv

.PHONY: help install hooks repl web transcripts transcripts-ci \
        test test-fast verify lint typecheck fmt fmt-check \
        clean reset replay status demo ship ci-status

help: ## Show this help
	awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## uv sync (incl. dev deps) + install pre-commit hooks
	$(UV) sync
	$(UV) run pre-commit install --install-hooks
	$(UV) run pre-commit install --hook-type pre-push

hooks: ## (Re)install git hooks only
	$(UV) run pre-commit install --install-hooks
	$(UV) run pre-commit install --hook-type pre-push

repl: ## Interactive CLI agent (CLIConfirmationChannel via /dev/tty)
	$(UV) run python -m agentic_cab repl

web: ## FastAPI + browser UI at http://localhost:8000 (WebConfirmationChannel)
	$(UV) run python -m agentic_cab web

transcripts: ## Regenerate all 5 transcripts from scenarios/
	$(UV) run python -m agentic_cab render-transcripts

transcripts-ci: ## Run scenarios and assert invariants only (no LLM, no markdown re-render)
	@if $(UV) run python -c "import agentic_cab.__main__" 2>/dev/null; then \
	  $(UV) run python -m agentic_cab render-transcripts --dry-run --assert-invariants; \
	else \
	  echo "skip: agentic_cab.__main__ not yet present (P0); will run from P1 onwards"; \
	fi

test: ## Full pytest suite
	$(UV) run pytest

test-fast: ## Unit + contract tests only (no scenarios, no slow)
	$(UV) run pytest -m "not scenario and not slow"

verify: fmt-check lint typecheck test-fast ## Full gate (used by pre-push)

lint: ## ruff check
	$(UV) run ruff check src tests

typecheck: ## pyright (strict on core/tools, basic elsewhere)
	$(UV) run pyright

fmt: ## ruff format (writes)
	$(UV) run ruff format src tests scripts
	$(UV) run ruff check --fix src tests

fmt-check: ## ruff format --check (no writes)
	$(UV) run ruff format --check src tests scripts

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .pyright dist build htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

reset: ## Wipe data/, logs/, chat/ and re-apply schema
	rm -rf data logs chat
	mkdir -p data logs chat
	$(UV) run python -m agentic_cab init-db

replay: ## Pretty-print a session log: `make replay SESSION=<id>`
	@test -n "$(SESSION)" || (echo "usage: make replay SESSION=<session_id>"; exit 1)
	$(UV) run python scripts/replay.py $(SESSION)

status: ## One-screen orientation: branch, dirty, unpushed, recent commits, per-phase counts
	echo "─── branch ───"
	git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(not a git repo)"
	echo "─── dirty ───"
	git status --short 2>/dev/null | head -20 || true
	echo "─── unpushed (committed locally, not yet shipped) ───"
	git log @{u}..HEAD --oneline 2>/dev/null || echo "(no upstream tracking branch set)"
	echo "─── recent ───"
	git log --oneline -10 2>/dev/null || true
	echo "─── shipped by phase (origin/main) ───"
	git log origin/main --oneline 2>/dev/null | grep -oE '\(P[0-9]\)' | sort | uniq -c | sort -rn || true

ship: ## Push current branch to origin and report CI status (use after `make verify` green)
	git push
	@sleep 2
	gh run list --limit 3 --branch "$$(git rev-parse --abbrev-ref HEAD)" 2>/dev/null || echo "(gh not installed or not authenticated)"

ci-status: ## Latest CI runs for current branch
	gh run list --limit 5 --branch "$$(git rev-parse --abbrev-ref HEAD)"

demo: ## Run the happy-path scenario and tail the action log
	$(UV) run python -m agentic_cab run-scenario 01_happy_path
