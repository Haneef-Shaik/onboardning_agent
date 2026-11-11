"""`eval_runs` (PRD §9): the run summaries the eval harness stores, read for HR_ADMIN (UI-8).

The harness writes them (`onb_evals.results.record_run`) through `TenantRepository` under the org
that owns the results; the API only reads, through `TenantRepository` in the token's tenant scope.
A stored run that does not read back as the contract raises: a run is never shown partially.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from onb_contracts.evals import EvalCaseSummary, EvalGate, EvalMetrics, EvalRun, EvalRunSummary
from onb_data import DataStore, TenantRepository

__all__ = ["EVAL_RUNS_COLLECTION", "EvalRunQuery", "EvalRunStore", "EvalRunsReader"]

# The harness's collection (`onb_evals.results.EVAL_RUNS_COLLECTION`; a test keeps them equal).
EVAL_RUNS_COLLECTION: Final = "eval_runs"
_NEWEST_FIRST: Final = [("started_at", -1), ("_id", -1)]
_SUMMARY_FIELDS: Final = (
    "mode",
    "agent",
    "llm",
    "dataset",
    "fixture",
    "repeat",
    "passed",
    "all_cases_passed",
)
_METRIC_FIELDS: Final = tuple(EvalMetrics.model_fields)


@dataclass(frozen=True, slots=True)
class EvalRunQuery:
    agent: str | None = None

    def filter(self) -> dict[str, Any]:
        return {} if self.agent is None else {"agent": self.agent}


class EvalRunsReader(Protocol):
    async def ensure_indexes(self) -> None:
        """Create the collection's indexes (idempotent); run at startup."""

    async def page(self, query: EvalRunQuery, *, page: int, limit: int) -> tuple[list[EvalRunSummary], int]:
        """One page of the current tenant's runs matching `query`, newest first, and the total."""

    async def get(self, run_id: str) -> EvalRun | None:
        """The current tenant's run with this id, or None."""


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _summary_fields(doc: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {name: doc[name] for name in _SUMMARY_FIELDS}
    return {
        "id": str(doc["_id"]),
        **fields,
        "started_at": _aware(doc["started_at"]),
        "finished_at": _aware(doc["finished_at"]),
    }


def summary_from_document(doc: Mapping[str, Any]) -> EvalRunSummary:
    return EvalRunSummary.model_validate(_summary_fields(doc))


def _metrics(groups: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, EvalMetrics]]:
    # The harness also stores exact fractions (`pass_at_1_exact`); the contract carries the rates.
    return {
        agent: {
            category: EvalMetrics.model_validate({name: values[name] for name in _METRIC_FIELDS})
            for category, values in by_category.items()
        }
        for agent, by_category in groups.items()
    }


def run_from_document(doc: Mapping[str, Any]) -> EvalRun:
    return EvalRun.model_validate(
        {
            **_summary_fields(doc),
            "metrics": _metrics(doc["metrics"]),
            "gates": [EvalGate.model_validate(gate) for gate in doc["gates"]],
            "cases": [EvalCaseSummary.model_validate(case) for case in doc["summary"]],
        }
    )


class EvalRunStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    def _repo(self) -> TenantRepository:
        return self._store.tenant(EVAL_RUNS_COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(EVAL_RUNS_COLLECTION, [("org_id", 1), ("started_at", -1), ("_id", -1)])

    async def page(self, query: EvalRunQuery, *, page: int, limit: int) -> tuple[list[EvalRunSummary], int]:
        repo = self._repo()
        found = query.filter()
        total = await repo.count(found)
        docs = await repo.find(found, sort=_NEWEST_FIRST, skip=(page - 1) * limit, limit=limit)
        return [summary_from_document(doc) for doc in docs], total

    async def get(self, run_id: str) -> EvalRun | None:
        doc = await self._repo().find_one({"_id": run_id})
        return None if doc is None else run_from_document(doc)
