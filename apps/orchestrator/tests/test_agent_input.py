"""AGT-1: what a task agent is asked (ADR 0021).

The mocks (MOCK-1) take a 24-hex `task_id` (their idempotency key) and a 24-hex `hire_id`, and the
laptop and account tools need the hire's role and location. A run's task ids are slugs and a hire's id
is `h_…`, so the executor hands the agent two opaque, stable references and the hire's non-PII
variables. Nothing else of the hire (name, mobile, notes, tokens) ever reaches the agent.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from onb_agents.runtime import AgentTask
from onb_data import tenant_scope
from onb_orchestrator.agent_input import opaque_ref

from .agent_support import HIRE, OK, ORG, Harness, Models, final, task_doc, template

HEX24 = re.compile(r"^[0-9a-f]{24}$")
SECRETS = ("Zed Synthetic", "9876500000", "ptk_secret_token", "private note about zed")


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


async def _add_hire(h: Harness, org: str = ORG, **extra: Any) -> None:
    hire = {
        "_id": HIRE,
        "name": SECRETS[0],
        "role": "engineer",
        "location": "bangalore",
        "start_date": "2026-12-01",
        "manager_id": "u_mgr_1",
        "notes": SECRETS[3],
        "pii": {"mobile": SECRETS[2]},
        "pii_masked": {"mobile": "******0000"},
        "mobile": SECRETS[1],
        **extra,
    }
    with tenant_scope(org):
        await h.store.tenant("new_hires").insert_one(hire)


def _ran(tmp_path: Path, *tasks: dict[str, Any], hire: bool = True) -> tuple[Harness, str]:
    h = Harness.create(Models([final(OK)], tmp_path))
    if hire:
        run(_add_hire(h))
    run_id = run(h.start(template(*tasks)))
    run(h.execute(run_id, h.executor()))
    return h, run_id


def _input(h: Harness, task_id: str) -> dict[str, Any]:
    [(task, _token)] = h.tools.for_task(task_id)
    assert isinstance(task, AgentTask)
    return dict(task.input)


def test_the_agent_gets_opaque_24_hex_references_for_the_task_and_the_hire(tmp_path: Path) -> None:
    h, _ = _ran(tmp_path, task_doc("laptop"))
    given = _input(h, "laptop")
    assert HEX24.match(given["task_key"])
    assert HEX24.match(given["hire_key"])
    assert given["task_key"] != given["hire_key"]
    assert "laptop" not in given["task_key"]
    assert HIRE not in given["hire_key"]


def test_the_references_are_stable_and_derived_from_org_run_and_task(tmp_path: Path) -> None:
    h, run_id = _ran(tmp_path, task_doc("a"), task_doc("b", "a"))
    a, b = _input(h, "a"), _input(h, "b")
    assert a["task_key"] == opaque_ref(ORG, "task", run_id, "a")
    assert a["task_key"] != b["task_key"]
    assert a["hire_key"] == b["hire_key"] == opaque_ref(ORG, "hire", HIRE)


def test_references_differ_by_org_run_and_kind() -> None:
    base = opaque_ref("org_a", "task", "run_1", "t")
    assert len({base, opaque_ref("org_b", "task", "run_1", "t"), opaque_ref("org_a", "task", "run_2", "t")}) == 3
    assert opaque_ref("org_a", "hire", "x") != opaque_ref("org_a", "task", "x")


def test_the_reference_parts_cannot_be_shifted_into_each_other() -> None:
    assert opaque_ref("org_a", "task", "ab", "c") != opaque_ref("org_a", "task", "a", "bc")
    # Even a part that contains the separator cannot be split into two others.
    assert opaque_ref("org_a", "task", "a\x00b", "c") != opaque_ref("org_a", "task", "a", "b\x00c")


def test_the_agent_gets_the_hires_non_pii_variables(tmp_path: Path) -> None:
    h, _ = _ran(tmp_path, task_doc("laptop"))
    assert _input(h, "laptop")["variables"] == {
        "role": "engineer",
        "location": "bangalore",
        "start_date": "2026-12-01",
        "manager_id": "u_mgr_1",
    }


def test_nothing_else_of_the_hire_reaches_the_agent(tmp_path: Path) -> None:
    h, _ = _ran(tmp_path, task_doc("laptop"))
    seen = json.dumps(_input(h, "laptop"), sort_keys=True)
    for secret in SECRETS:
        assert secret not in seen
    assert set(_input(h, "laptop")) == {"action", "title", "inputs", "hire_id", "task_key", "hire_key", "variables"}


def test_a_hire_of_another_org_is_never_read(tmp_path: Path) -> None:
    h = Harness.create(Models([final(OK)], tmp_path))
    run(_add_hire(h, org="org_other", role="sales"))
    run_id = run(h.start(template(task_doc("laptop"))))
    run(h.execute(run_id, h.executor()))
    assert _input(h, "laptop")["variables"] == {}


def test_a_run_without_a_hire_record_still_runs_with_no_variables(tmp_path: Path) -> None:
    h, _ = _ran(tmp_path, task_doc("laptop"), hire=False)
    assert _input(h, "laptop")["variables"] == {}
    assert _input(h, "laptop")["task_key"]
