"""Run and task records built from a compiled DAG (RUN-1; PRD FR-R1, §7.1 Compilation, ADR 0008)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
from onb_agents.dag import CompiledDag, compile_dag
from onb_agents.runs import (
    DagIntegrityError,
    hire_variables,
    new_run_id,
    run_records,
    stored_dag,
    thread_id,
)
from onb_agents.runs.testing import diamond_template, hire_variables_for

NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


def _records(role: str = "sales") -> tuple[CompiledDag, dict[str, object], tuple[dict[str, object], ...]]:
    dag = compile_dag(diamond_template(), hire_variables_for(role))
    records = run_records(
        org_id="org_test", run_id="run_0001", hire_id="h_1", dag=dag, actor_id="u_hr", traceparent=None, now=NOW
    )
    return dag, records.run, records.tasks


def test_the_thread_id_is_org_id_colon_run_id() -> None:
    assert thread_id("org_acme", "run_abc") == "org_acme:run_abc"


@pytest.mark.parametrize(("org", "run"), [("", "run_1"), ("org_a", ""), ("org:a", "run_1"), ("org_a", "run:1")])
def test_a_thread_id_part_that_is_empty_or_holds_a_colon_is_refused(org: str, run: str) -> None:
    with pytest.raises(ValueError, match="thread id"):
        thread_id(org, run)


def test_run_ids_are_random_and_well_formed() -> None:
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("run_") and len(i) == 28 for i in ids)


def test_the_run_record_pins_the_version_and_the_hash() -> None:
    dag, run, _ = _records()
    assert run["_id"] == "run_0001"
    assert run["template_key"] == "diamond-test"
    assert run["template_version"] == 1
    assert run["dag_hash"] == dag.hash
    assert run["dag"] == dag.document(include_hash=False)
    assert run["thread_id"] == "org_test:run_0001"
    assert run["status"] == "started"
    assert run["created_by"] == "u_hr"
    assert run["created_at"] == NOW
    assert "org_id" not in run  # the tenant repository sets it


def test_one_task_record_per_compiled_task_including_skipped_ones() -> None:
    dag, _, tasks = _records("sales")
    assert [t["task_id"] for t in tasks] == [t.id for t in dag.tasks]
    by_id = {t["task_id"]: t for t in tasks}
    assert by_id["e"]["status"] == "skipped"
    assert "role == 'engineer'" in str(by_id["e"]["skip_reason"])
    assert by_id["f"]["status"] == "pending"  # its own `when` decides; skipping does not cascade
    assert by_id["d"]["depends_on"] == ["b", "c"]
    assert by_id["a"]["due"] == "2026-11-24"
    assert [t["position"] for t in tasks] == list(range(len(tasks)))
    assert all(t["_id"] == f"run_0001:{t['task_id']}" and t["run_id"] == "run_0001" for t in tasks)
    assert all(t["attempts"] == 0 and t["started_at"] is None and t["finished_at"] is None for t in tasks)


def test_the_hash_is_recomputed_before_persisting_and_a_changed_dag_is_refused() -> None:
    dag = compile_dag(diamond_template(), hire_variables_for())
    # A caller changed a compiled task after compiling (ADR 0008: compiled tasks are read-only).
    dag.tasks[0].task.inputs["injected"] = "x"
    with pytest.raises(DagIntegrityError):
        run_records(org_id="o", run_id="r", hire_id="h", dag=dag, actor_id="u", traceparent=None, now=NOW)


def test_a_dag_with_a_forged_hash_is_refused() -> None:
    dag = dataclasses.replace(compile_dag(diamond_template(), hire_variables_for()), hash="sha256:" + "0" * 64)
    with pytest.raises(DagIntegrityError):
        run_records(org_id="o", run_id="r", hire_id="h", dag=dag, actor_id="u", traceparent=None, now=NOW)


def test_the_stored_dag_is_returned_only_when_its_hash_still_matches() -> None:
    _, run, _ = _records()
    assert stored_dag(run) == run["dag"]
    tampered = {**run, "dag": {**run["dag"], "order": list(reversed(run["dag"]["order"]))}}
    with pytest.raises(DagIntegrityError):
        stored_dag(tampered)
    with pytest.raises(DagIntegrityError):
        stored_dag({**run, "dag": "not a document"})


def test_the_run_record_does_not_share_state_with_the_compiled_dag() -> None:
    dag, run, _ = _records()
    dag.tasks[0].task.inputs["late"] = "change"
    assert "late" not in run["dag"]["tasks"][0]["inputs"]  # type: ignore[index]
    assert stored_dag(run) == run["dag"]


def test_only_declared_hire_fields_become_variables_never_pii() -> None:
    hire = {
        "_id": "h_1",
        "name": "Test Person",
        "role": "engineer",
        "location": "Testville",
        "start_date": "2026-12-01",
        "manager_id": "u_m",
        "pii": {"email": "ptk_x"},
        "email_hash": "abc",
    }
    assert hire_variables(hire) == {
        "role": "engineer",
        "location": "Testville",
        "start_date": "2026-12-01",
        "manager_id": "u_m",
    }
    assert hire_variables({"role": "sales"}) == {"role": "sales"}
