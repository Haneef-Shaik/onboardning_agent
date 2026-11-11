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

import copy
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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

# UI-8: eval runs stored by the harness's writer, (org, run id, agent). B has 3, A has 2, and B's agent
# name and run ids occur nowhere in A's data.
EVAL_RUNS: tuple[tuple[str, str, str], ...] = (
    (ORG_A, "evr_20261101T080000Z_acac0001", "db_agent"),
    (ORG_A, "evr_20261102T080000Z_acac0002", "run_assistant"),
    (ORG_B, "evr_20261101T090000Z_6b6b0001", "globex_eval_agent"),
    (ORG_B, "evr_20261102T090000Z_6b6b0002", "globex_eval_agent"),
    (ORG_B, "evr_20261103T090000Z_6b6b0003", "db_agent"),
)


def eval_runs(org_id: str) -> list[tuple[str, str, str]]:
    return [row for row in EVAL_RUNS if row[0] == org_id]


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


# seed_v1 gives org B no approval; one is added here so every counted collection can show a leak.
EXTRA_DOCUMENTS: dict[str, tuple[dict[str, Any], ...]] = {
    "approvals": (
        {
            "_id": "appr_globex_xt",
            "org_id": ORG_B,
            "run_id": "run_globex_001",
            "task_id": "manager_approval",
            "approver_id": "u_globex_mgr1",
            "status": "pending",
        },
    ),
}


# HIRE-1: one hire per org created through the hire service, so its values sit in the real vault and
# reveal, update and the access log have something to work on. Synthetic values (PII-3 ranges).
XT_VAULT_HIRES: dict[str, dict[str, Any]] = {
    ORG_A: {
        "name": "Meera Iyer",
        "manager_id": "u_acme_mgr_ravi",
        "pii": {"email": "meera.iyer.xt@example.com", "mobile": "+91 90000 00061", "pan": "AXTPI6101K"},
    },
    ORG_B: {
        "name": "Oskar Lindqvist",
        "manager_id": "u_globex_mgr1",
        "pii": {"email": "oskar.lindqvist.xt@example.org", "mobile": "+91 90000 00062", "pan": "BXTPL6202L"},
    },
}


def xt_vault_strings(org_id: str) -> list[str]:
    """The org's vault hire values in every layout the API could show: as typed and canonical."""
    hire = XT_VAULT_HIRES[org_id]
    mobile = hire["pii"]["mobile"].replace(" ", "")
    return [hire["name"], *hire["pii"].values(), hire["pii"]["email"].lower(), mobile, mobile.removeprefix("+91")]


def tenant_documents(org_id: str) -> dict[str, list[dict[str, Any]]]:
    """The seed's tenant documents of one org (plus `EXTRA_DOCUMENTS`), by collection, as stored."""
    data = seed()
    return {
        name: [doc for doc in (*data[name], *EXTRA_DOCUMENTS.get(name, ())) if doc["org_id"] == org_id]
        for name in TENANT_SEED
    }


def audit_rows(org_id: str) -> list[tuple[str, str, str, str, str, str]]:
    return [row for row in AUDIT_ROWS if row[0] == org_id]


# --- templates (TPL-2) ----------------------------------------------------------------------------------
# Each org's drafts, in creation order: (draft id, template body, publish it?). Org A has one published
# version of `engineer-india` and one open draft. Org B has three versions of the same key, so a
# version counter shared across orgs would show up in org A's numbers, plus a template of its own.

SEED_TEMPLATE = ROOT / "templates" / "engineer-india.yaml"
A_DRAFT = "tdr_" + "a" * 32
A_PUBLISHED_DRAFT = "tdr_" + "a" * 31 + "1"
B_DRAFT = "tdr_" + "b" * 32
B_SALES_DRAFT = "tdr_" + "b" * 31 + "1"
B_ENGINEER_DRAFTS = tuple("tdr_" + "b" * 31 + str(n) for n in (2, 3, 4))
SHARED_KEY = "engineer-india"
B_ONLY_KEY = "globex-xt-sales"
B_ENGINEER_VERSIONS = len(B_ENGINEER_DRAFTS)


def base_template() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    return data


def _b_sales_template() -> dict[str, Any]:
    template = copy.deepcopy(base_template())
    template["key"] = B_ONLY_KEY
    template["name"] = "Globex XT Field Sales"
    template["tasks"][0]["title"] = "Globex XT paperwork pickup"
    return template


def template_drafts(org_id: str) -> list[tuple[str, dict[str, Any], bool]]:
    if org_id == ORG_A:
        return [(A_DRAFT, base_template(), False), (A_PUBLISHED_DRAFT, base_template(), True)]
    return [
        (B_DRAFT, _b_sales_template(), False),
        (B_SALES_DRAFT, _b_sales_template(), True),
        *((draft_id, base_template(), True) for draft_id in B_ENGINEER_DRAFTS),
    ]


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
    data = seed()
    users = [{**u, "email": email_of(u["_id"])} for u in seed_users() if u["org_id"] == org_id]
    # The org's name and its raw sensitive values: a reveal or document route must never show B's.
    org = [o for o in data["orgs"] if o["_id"] == org_id]
    vault = [v for v in data["pii_vault_plain"] if v["org_id"] == org_id]
    templates = template_drafts(org_id)
    sources = [
        org_id,
        org,
        users,
        vault,
        tenant_documents(org_id),
        audit_rows(org_id),
        xt_vault_strings(org_id),
        templates,
        eval_runs(org_id),
    ]
    return {s for s in _strings(sources) if len(s) >= _MIN_MARKER}


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
