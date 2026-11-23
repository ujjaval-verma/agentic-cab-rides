# Makefile is the discovery surface for this repo.
# `make help` lists every target. Hooks call into this; no logic duplication.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
.SILENT: help status

UV ?= uv

.PHONY: help install hooks link-env repl web transcripts transcripts-ci \
        test test-fast verify audit lint typecheck fmt fmt-check \
        clean reset replay status demo ship ci-status

ENV_SRC := $(HOME)/.config/agentic-cab-rides/.env

help: ## Show this help
	awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## uv sync (incl. dev deps) + install pre-commit hooks
	$(UV) sync
	$(UV) run pre-commit install --install-hooks
	$(UV) run pre-commit install --hook-type pre-push
	# Auto-prune stale `origin/...` refs on `git fetch` so deleted upstream
	# branches don't linger locally. Paired with GitHub's
	# "Automatically delete head branches on merge" setting on the repo.
	git config --local fetch.prune true
	@if [ ! -e .env ] && [ -f "$(ENV_SRC)" ]; then \
		echo ""; \
		echo "tip: $(ENV_SRC) exists but this worktree has no .env."; \
		echo "     run \`make link-env\` to symlink it."; \
	fi

hooks: ## (Re)install git hooks only
	$(UV) run pre-commit install --install-hooks
	$(UV) run pre-commit install --hook-type pre-push

link-env: ## Symlink ./.env -> ~/.config/agentic-cab-rides/.env (shared across worktrees)
	@if [ ! -f "$(ENV_SRC)" ]; then \
		echo "error: $(ENV_SRC) does not exist"; \
		echo "       first-time setup: mkdir -p $$(dirname $(ENV_SRC)) && chmod 700 $$(dirname $(ENV_SRC))"; \
		echo "                         cp .env.example $(ENV_SRC) && chmod 600 $(ENV_SRC)"; \
		echo "                         then edit $(ENV_SRC) and populate ANTHROPIC_API_KEY"; \
		exit 1; \
	fi
	@if [ -L .env ]; then \
		if [ "$$(readlink .env)" = "$(ENV_SRC)" ]; then \
			echo ".env already linked to $(ENV_SRC)"; \
			exit 0; \
		fi; \
		echo "error: .env is a symlink pointing elsewhere ($$(readlink .env))"; \
		echo "       remove it manually if you want to relink"; \
		exit 1; \
	fi
	@if [ -e .env ]; then \
		echo "error: .env exists as a regular file in this worktree"; \
		echo "       remove it manually (rm .env) if you want to switch to the symlinked secret"; \
		exit 1; \
	fi
	ln -s "$(ENV_SRC)" .env
	@echo "linked .env -> $(ENV_SRC)"

repl: ## Interactive CLI agent (CLIConfirmationChannel via /dev/tty)
	$(UV) run python -m agentic_cab repl

web: ## FastAPI + browser UI at http://localhost:8000 (WebConfirmationChannel)
	$(UV) run uvicorn agentic_cab.web.app:create_app --factory --host 127.0.0.1 --port 8000 --reload

transcripts: ## Regenerate all 6 transcripts from scenarios/
	$(UV) run python -m agentic_cab render-transcripts

transcripts-ci: ## Run scenarios and assert invariants only (no LLM, no markdown re-render)
	@if $(UV) run python -m agentic_cab render-transcripts --help 2>/dev/null | grep -q render-transcripts; then \
	  $(UV) run python -m agentic_cab render-transcripts --dry-run --assert-invariants; \
	else \
	  echo "skip: render-transcripts not yet wired (P0/P1); will run from P2 onwards"; \
	fi

test: ## Full pytest suite
	$(UV) run pytest

test-fast: ## Unit + contract tests only (no scenarios, no slow)
	$(UV) run pytest -m "not scenario and not slow"

verify: fmt-check lint typecheck test-fast ## Full gate (used by pre-push)

# Extra flags for scripts/audit_deps.py. CI passes AUDIT_FLAGS=--strict so an
# audit that cannot reach the advisory service fails instead of warning.
AUDIT_FLAGS ?=

audit: ## pip-audit over uv.lock (warns if offline, fails on a vulnerability; AUDIT_FLAGS=--strict fails offline too)
	$(UV) run python scripts/audit_deps.py $(AUDIT_FLAGS)

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
	$(UV) run python scripts/replay.py "$(SESSION)"

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

demo: ## Start web UI and open browser — manual smoke test  # manual smoke
	@echo ""
	@echo "  ╔══════════════════════════════════════════════════════════╗"
	@echo "  ║  agentic-cab-rides — web demo                           ║"
	@echo "  ║  Open http://127.0.0.1:8000 in your browser.            ║"
	@echo "  ║  Try: book a ride, track it, then cancel.               ║"
	@echo "  ║  Ctrl-C to stop the server.                             ║"
	@echo "  ╚══════════════════════════════════════════════════════════╝"
	@echo ""
	$(UV) run python -m webbrowser http://127.0.0.1:8000 &
	$(UV) run uvicorn agentic_cab.web.app:create_app --factory --host 127.0.0.1 --port 8000
