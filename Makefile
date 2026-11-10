# Onboarding Platform: developer entry points (PRD §11, §12; MASTER-PLAN §7).
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := check

PYTHON_VERSION := 3.12
NX := node_modules/.bin/nx
export NX_DAEMON ?= false
export NX_NO_CLOUD := true
export NX_TUI := false

.PHONY: bootstrap check plan-check tooling-test lint typecheck test test-int fmt

## Install the pinned toolchains: one uv virtualenv at the root, and node_modules.
bootstrap:
	uv python install $(PYTHON_VERSION)
	uv sync --frozen
	npm ci --no-audit --no-fund

## The standard gate: plan check, repo guards, then lint, typecheck and unit tests (with the
## 80% line-coverage threshold on every libs/py-* project) for all 17 Nx projects.
check: plan-check tooling-test lint typecheck test
	@echo "make check: all passed"

plan-check:
	python3 scripts/plan/plan.py check

tooling-test:
	uv run --frozen ruff check tools
	uv run --frozen ruff format --check tools
	uv run --frozen mypy tools
	uv run --frozen pytest tools/tests scripts/plan/test_plan.py -q

lint:
	$(NX) run-many -t lint --output-style=static

typecheck:
	$(NX) run-many -t typecheck --output-style=static

test:
	$(NX) run-many -t test --output-style=static

## Integration tests run every project's `test-int` target (added by F-2 with compose + testcontainers).
test-int:
	$(NX) run-many -t test-int --output-style=static

fmt:
	uv run --frozen ruff check --fix apps libs tools
	uv run --frozen ruff format apps libs tools
	node_modules/.bin/eslint --fix apps libs
