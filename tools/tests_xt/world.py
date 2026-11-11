"""The two-org world every cross-tenant case runs in, and the leak checks (PRD §7.9 Testing).

Org A (`org_acme`) is the actor's org; org B (`org_globex`) is the one it must never reach. The
tenant documents come from `evals/fixtures/seed_v1.json` (synthetic people only); the audit records
are made up here, with B holding more of them than A, so a total that includes B shows up as a wrong
number rather than a coincidence.

`b_markers()` is every distinctive string in org B's seeded data (ids, names, tokens, audit actors
and targets) that never occurs in org A's. A response an org-A actor receives must contain none of
them (`assert_no_leak`). `assert_indistinguishable` is the inference check: asking about something
of org B must look exactly like asking about something that does not exist.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "evals" / "fixtures" / "seed_v1.json"

ORG_A = "org_acme"
ORG_B = "org_globex"

# Seed collections loaded into the scratch database, as tenant documents of their own org.
TENANT_SEED = ("new_hires", "onboarding_runs", "run_tasks", "approvals")

# (org, actor type, actor id, action, target, outcome). B has 5 records, A has 3.
AUDIT_ROWS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (ORG_A, "user", "u_acme_hr1", "pii.reveal", "hire:h_acme_001/pan", "success"),
    (ORG_A, "agent", "db_agent", "mcp.tool_call", "find:new_hires", "denied"),
    (ORG_A, "user", "u_acme_hradmin", "run.manual_complete", "run:run_acme_001/t_2", "success"),
    (ORG_B, "user", "u_globex_hr1", "pii.reveal", "hire:h_globex_001/pan", "success"),
    (ORG_B, "user", "u_globex_hr1", "xt.globex_only", "hire:h_globex_001/bank_account", "success"),
    (ORG_B, "agent", "globex_doc_agent", "mcp.tool_call", "find:globex_ledger", "failed"),
    (ORG_B, "user", "u_globex_mgr1", "run.manual_complete", "run:run_globex_001/t_9", "denied"),
    (ORG_B, "user", "u_globex_hr1", "pii.reveal", "hire:h_globex_001/ifsc", "success"),
)

# Shortest string treated as a marker: shorter ones ("HR", "done") occur everywhere by chance.
_MIN_MARKER = 6


def seed() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SEED.read_text())
    return data


def seed_users() -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = seed()["users"]
    return users


def email_of(user_id: str) -> str:
    return f"{user_id}@example.com"


def tenant_documents(org_id: str) -> dict[str, list[dict[str, Any]]]:
    """The seed's tenant documents of one org, by collection (with `org_id`, as stored)."""
    data = seed()
    return {name: [doc for doc in data[name] if doc["org_id"] == org_id] for name in TENANT_SEED}


def audit_rows(org_id: str) -> list[tuple[str, str, str, str, str, str]]:
    return [row for row in AUDIT_ROWS if row[0] == org_id]


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, Iterable):
        for item in value:
            yield from _strings(item)


def _org_strings(org_id: str) -> set[str]:
    users = [{**u, "email": email_of(u["_id"])} for u in seed_users() if u["org_id"] == org_id]
    return {s for s in _strings([org_id, users, tenant_documents(org_id), audit_rows(org_id)]) if len(s) >= _MIN_MARKER}


def b_markers() -> frozenset[str]:
    """Strings found in org B's seeded data and nowhere in org A's."""
    return frozenset(_org_strings(ORG_B) - _org_strings(ORG_A))


def assert_no_leak(text: str, what: str) -> None:
    leaked = sorted(m for m in b_markers() if m in text)
    assert leaked == [], f"{what}: org B data reached an org-A actor: {leaked}"


@dataclass(frozen=True)
class Seen:
    """What a caller observes of one request: the status and the body, minus fields that differ by
    design on every call (a request id, a timestamp)."""

    status: int
    body: object


VOLATILE_KEYS = frozenset({"request_id", "trace_id", "at", "timestamp"})


def without_volatile(value: object) -> object:
    if isinstance(value, Mapping):
        return {k: without_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [without_volatile(v) for v in value]
    return value


def assert_indistinguishable(foreign: Seen, missing: Seen, what: str) -> None:
    """403-vs-404 and friends: org B's thing must look exactly like a thing that does not exist."""
    assert foreign == missing, f"{what}: org B's data is distinguishable from missing data"
