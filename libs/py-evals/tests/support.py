"""Test helpers: a check context over in-memory state and vault, with facts from the real seed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from onb_evals.capture import AuditEntry, CaseCapture, ToolCall
from onb_evals.cases import CheckSpec, EvalCase, parse_case
from onb_evals.checks import run_check
from onb_evals.checks.base import CheckResult
from onb_evals.context import CheckContext
from onb_evals.contracts import DEFAULT_CONTRACTS
from onb_evals.facts import facts_from_seed
from onb_evals.paths import resolve

REPO = Path(__file__).resolve().parents[3]
SEED_PATH = REPO / "evals" / "fixtures" / "seed_v1.json"
DATASET = REPO / "evals" / "datasets" / "onboarding_agents_v1.jsonl"
SEED: dict[str, Any] = json.loads(SEED_PATH.read_text())
FACTS = facts_from_seed(SEED)


def engineer_template() -> dict[str, Any]:
    template = next(t for t in SEED["templates"] if t["key"] == "engineer-india")
    return {k: v for k, v in json.loads(json.dumps(template)).items() if k != "org_id"}


class MemoryState:
    """`StateReader` over plain documents; equality filters on dotted paths."""

    def __init__(self, collections: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> None:
        self._collections = {name: [dict(d) for d in docs] for name, docs in (collections or {}).items()}

    async def find(self, collection: str, query: Mapping[str, Any], *, org_id: str) -> list[dict[str, Any]]:
        scoped = {**query, "org_id": org_id}
        return [
            dict(doc)
            for doc in self._collections.get(collection, [])
            if all(resolve(doc, key) == value for key, value in scoped.items())
        ]


class MemoryVault:
    """`VaultProbe` over the seed's plain records: tokens are the seed names."""

    def __init__(self, records: Sequence[Mapping[str, Any]] = SEED["pii_vault_plain"]) -> None:
        self._owners = {r["token"]: (r["org_id"], r["subject_id"], r["field"]) for r in records}

    def owner(self, token: str) -> tuple[str, str, str] | None:
        return self._owners.get(token)

    async def is_bound(self, token: str, *, org_id: str, subject_id: str, field: str) -> bool:
        return self._owners.get(token) == (org_id, subject_id, field)


def make_case(
    checks: Sequence[Mapping[str, Any]] = ({"type": "status_in", "values": ["ok"]},), **over: Any
) -> EvalCase:
    raw: dict[str, Any] = {
        "id": "t-001",
        "agent": "db_agent",
        "category": "capability",
        "actor": {"org_id": "org_acme", "role": "SYSTEM"},
        "input": {"message": "test"},
        "checks": list(checks),
        **over,
    }
    return parse_case(raw)


def context(
    output: Mapping[str, Any] | None = None,
    *,
    case: EvalCase | None = None,
    tool_calls: Sequence[ToolCall] = (),
    audit: Sequence[AuditEntry] = (),
    llm_inputs: Sequence[str] = (),
    trace: Sequence[str] = (),
    steps: int = 1,
    state: MemoryState | None = None,
) -> CheckContext:
    capture = CaseCapture(
        output=output,
        tool_calls=tuple(tool_calls),
        llm_inputs=tuple(llm_inputs),
        steps=steps,
        audit=tuple(audit),
        trace=tuple(trace),
    )
    return CheckContext(
        case=case or make_case(),
        capture=capture,
        state=state or MemoryState(),
        vault=MemoryVault(),
        facts=FACTS,
        contracts=DEFAULT_CONTRACTS,
    )


def spec(raw: Mapping[str, Any]) -> CheckSpec:
    return make_case([raw]).checks[0]


def run(raw_check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    return asyncio.run(run_check(spec(raw_check), ctx))
