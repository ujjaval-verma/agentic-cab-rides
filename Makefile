# Makefile is the discovery surface for this repo.
# `make help` lists every target. Hooks call into this; no logic duplication.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.SILENT: help status

UV ?= uv

.PHONY: help install hooks repl web transcripts transcripts-ci \
        test test-fast verify lint typecheck fmt fmt-check \
        clean reset replay status demo

help: ## Show this help
	awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## uv sync + install pre-commit hooks
	$(UV) sync --all-extras
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
	$(UV) run python -m agentic_cab render-transcripts --dry-run --assert-invariants

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

reset: ## Wipe runtime state (data/, logs/, chat/)
	rm -rf data logs chat
	mkdir -p data logs chat
	touch data/.gitkeep logs/.gitkeep chat/.gitkeep

replay: ## Pretty-print a session log: `make replay SESSION=<id>`
	@test -n "$(SESSION)" || (echo "usage: make replay SESSION=<session_id>"; exit 1)
	$(UV) run python scripts/replay.py $(SESSION)

status: ## One-screen orientation: branch, dirty files, recent commits, slice tracker
	echo "─── branch ───"
	git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(not a git repo)"
	echo "─── dirty ───"
	git status --short 2>/dev/null | head -20 || true
	echo "─── recent ───"
	git log --oneline -5 2>/dev/null || true
	echo "─── slice ───"
	if [ -f docs/build-progress.md ]; then grep -E "^\- \[[ \-x]\]" docs/build-progress.md | head -10; else echo "(docs/build-progress.md not yet scaffolded — see writing-plans step)"; fi

demo: ## Run the happy-path scenario and tail the action log
	$(UV) run python -m agentic_cab run-scenario 01_happy_path
