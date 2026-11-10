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

.PHONY: bootstrap check plan-check env-check tooling-test contracts contracts-check lint typecheck test test-int evals-selftest fmt up down logs llm-health

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
	uv run --frozen ruff check tools scripts/check_env_example.py scripts/contracts.py
	uv run --frozen ruff format --check tools scripts/check_env_example.py scripts/contracts.py
	uv run --frozen mypy tools scripts/check_env_example.py scripts/contracts.py
	uv run --frozen pytest tools/tests scripts/plan/test_plan.py -q

## API contracts (PRD §11): the OpenAPI document and the TS client generated from it.
## `contracts` regenerates both; `contracts-check` fails when either is stale against the API.
contracts:
	uv run --frozen python scripts/contracts.py generate

contracts-check:
	uv run --frozen python scripts/contracts.py check

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

## Eval harness self-test (PRD §8.2, MASTER-PLAN §7): every dataset case against its scripted output on
## the `fake` connector (no model calls), k=3, each repetition in a fresh database on the compose Mongo,
## plus negative controls that must fail. Writes evals/reports/ and an eval_runs record.
evals-selftest: up
	set -a && . ./$(ENV_FILE) && set +a && \
	LLM_CONNECTOR=fake OTEL_TRACES_EXPORTER="$${OTEL_TRACES_EXPORTER:-none}" \
	uv run --frozen pytest libs/py-evals/tests_int/test_selftest.py -q -s -o cache_dir=.pytest_cache/py-evals-selftest

## Base stack (PRD §7.11): mongo (replica set, auth, keyfile), otel-collector, jaeger, caddy.
## `.env` names the compose file, project and profile, so plain `docker compose …` works too once
## this target has generated the Mongo keyfile. Needs Docker Engine 25+ (healthcheck start_interval).
up: $(ENV_FILE)
	infra/mongo/gen-keyfile.sh
	$(COMPOSE) up -d --wait --build

## Stops the stack; data volumes are kept.
down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

## Call every model alias once through one connector (PRD §7.1), e.g. `make llm-health CONNECTOR=bedrock`.
## Loads .env when present; LLM_CONNECTOR is set to CONNECTOR so every alias goes through it.
llm-health:
	@[[ "$$CONNECTOR" =~ ^[a-z0-9_-]+$$ ]] || { echo "usage: make llm-health CONNECTOR=<bedrock|langchain|fake|…>" >&2; exit 2; }
	set -a && { [ ! -f ./$(ENV_FILE) ] || . ./$(ENV_FILE); } && set +a && \
	LLM_CONNECTOR="$$CONNECTOR" uv run --frozen python -m onb_llm.health --connector "$$CONNECTOR"

$(ENV_FILE):
	@echo "No $(ENV_FILE): copy .env.example to $(ENV_FILE) and fill in the empty values." >&2
	@exit 1

fmt:
	uv run --frozen ruff check --fix apps libs tools
	uv run --frozen ruff format apps libs tools
	node_modules/.bin/eslint --fix apps libs
