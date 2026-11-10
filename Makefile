# Onboarding Platform: developer entry points (PRD §11, §12; MASTER-PLAN §7).
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := check

PYTHON_VERSION := 3.12
NX := node_modules/.bin/nx
export NX_DAEMON ?= false
export NX_NO_CLOUD := true
export NX_TUI := false

COMPOSE := docker compose
ENV_FILE := .env

.PHONY: bootstrap check plan-check env-check tooling-test lint typecheck test test-int fmt up down logs

## Install the pinned toolchains: one uv virtualenv at the root, and node_modules.
bootstrap:
	uv python install $(PYTHON_VERSION)
	uv sync --frozen
	npm ci --no-audit --no-fund

## The standard gate: plan check, repo guards, then lint, typecheck and unit tests (with the
## 80% line-coverage threshold on every libs/py-* project) for all 17 Nx projects.
check: plan-check env-check tooling-test lint typecheck test
	@echo "make check: all passed"

plan-check:
	python3 scripts/plan/plan.py check

## Every variable infra/docker-compose.yml uses must be documented in .env.example (PRD §7.11).
env-check:
	python3 scripts/check_env_example.py

tooling-test:
	uv run --frozen ruff check tools scripts/check_env_example.py
	uv run --frozen ruff format --check tools scripts/check_env_example.py
	uv run --frozen mypy tools scripts/check_env_example.py
	uv run --frozen pytest tools/tests scripts/plan/test_plan.py -q

lint:
	$(NX) run-many -t lint --output-style=static

typecheck:
	$(NX) run-many -t typecheck --output-style=static

test:
	$(NX) run-many -t test --output-style=static

## Integration tests: bring the compose stack up, load .env, then run the stack checks in
## tools/tests_int and every project's `test-int` target against the compose services.
test-int: up
	set -a && . ./$(ENV_FILE) && set +a && \
	uv run --frozen pytest tools/tests_int -q -o cache_dir=.pytest_cache/tools-int && \
	$(NX) run-many -t test-int --output-style=static

## Base stack (PRD §7.11): mongo (replica set, auth, keyfile), otel-collector, jaeger, caddy.
## `.env` names the compose file, project and profile, so plain `docker compose …` works too.
up: $(ENV_FILE)
	infra/mongo/gen-keyfile.sh
	$(COMPOSE) up -d --wait --build

## Stops the stack; data volumes are kept.
down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

$(ENV_FILE):
	@echo "No $(ENV_FILE): copy .env.example to $(ENV_FILE) and fill in the empty values." >&2
	@exit 1

fmt:
	uv run --frozen ruff check --fix apps libs tools
	uv run --frozen ruff format apps libs tools
	node_modules/.bin/eslint --fix apps libs
