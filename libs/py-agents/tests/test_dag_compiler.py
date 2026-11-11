"""DAG compilation (DAG-1 boxes 1, 3 and 4; PRD §5.1, §7.1 Compilation, FR-R3).

A published template version plus a hire's variables compile into a pruned, deterministic DAG: tasks
whose `when` fails are `skipped` with a reason, never dropped, and never block their dependents.
The template is the seed template (`templates/engineer-india.yaml`) published as version 1.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from onb_agents.dag import CompiledDag, CompileError, CompileReason, compile_dag
from onb_contracts.templates import Template

REPO = Path(__file__).resolve().parents[3]
SEED_TEMPLATE = REPO / "templates" / "engineer-india.yaml"


def seed_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED_TEMPLATE.read_text())
    return {**data, "version": 1, "status": "published"}


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.model_validate(seed_data())


def hire(**overrides: object) -> dict[str, object]:
    """Synthetic hire variables (no real person); `start_date` is an ISO date as stored."""
    base: dict[str, object] = {
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-01",
        "manager_id": "u_acme_mgr_ravi",
    }
    return {**base, **overrides}


# --- box 1: engineer keeps bitbucket_access; sales skips it with a reason -------------------------


def test_engineer_variables_keep_bitbucket_access(template: Template) -> None:
    dag = compile_dag(template, hire(role="engineer"))
    task = dag.task("bitbucket_access")
    assert task.status == "pending"
    assert task.skip_reason is None
    assert "bitbucket_access" in dag.ready(done={"collect_docs", "manager_approval"})


def test_sales_variables_mark_bitbucket_access_skipped_with_a_reason(template: Template) -> None:
    dag = compile_dag(template, hire(role="sales"))
    task = dag.task("bitbucket_access")
    assert task.status == "skipped"
    assert task.skip_reason is not None
    assert "when" in task.skip_reason
    assert "role == 'engineer'" in task.skip_reason
    assert "sales" not in task.skip_reason  # the reason names the condition, not the hire's values
    assert "bitbucket_access" not in dag.ready(done={"collect_docs", "manager_approval"})


def test_a_skipped_task_stays_in_the_dag_with_its_edges(template: Template) -> None:
    dag = compile_dag(template, hire(role="sales"))
    assert [task.id for task in dag.tasks] == [task.id for task in template.tasks]
    assert dag.task("bitbucket_access").depends_on == ("manager_approval",)
    assert {task.id for task in dag.tasks if task.status == "skipped"} == {"bitbucket_access", "security_training"}


def test_a_designer_keeps_security_training_and_skips_bitbucket(template: Template) -> None:
    dag = compile_dag(template, hire(role="designer"))
    assert dag.task("security_training").status == "pending"
    assert dag.task("bitbucket_access").status == "skipped"


def test_tasks_without_a_condition_are_always_pending(template: Template) -> None:
    dag = compile_dag(template, hire(role="sales"))
    for task_id in ("collect_docs", "manager_approval", "laptop", "orientation", "welcome_message"):
        assert dag.task(task_id).status == "pending"


def test_a_dependent_of_a_skipped_task_is_not_blocked() -> None:
    data = seed_data()
    data["tasks"] = [
        *data["tasks"],
        {
            "id": "repo_tour",
            "title": "Tour of the repositories",
            "agent": "db_agent",
            "action": "schedule_orientation",
            "depends_on": ["bitbucket_access"],
        },
    ]
    dag = compile_dag(Template.model_validate(data), hire(role="sales"))
    assert dag.task("repo_tour").status == "pending"
    assert "repo_tour" in dag.ready(done={"collect_docs", "manager_approval"})


def test_ready_waits_for_every_unfinished_dependency(template: Template) -> None:
    dag = compile_dag(template, hire(role="engineer"))
    assert dag.ready(done=set()) == ("collect_docs", "orientation")
    assert dag.ready(done={"collect_docs"}) == ("manager_approval", "orientation")
    assert "laptop" not in dag.ready(done={"collect_docs"})


def test_ready_refuses_an_unknown_task_id(template: Template) -> None:
    dag = compile_dag(template, hire())
    with pytest.raises(KeyError):
        dag.ready(done={"no_such_task"})


def test_the_order_puts_every_task_after_its_dependencies(template: Template) -> None:
    dag = compile_dag(template, hire())
    position = {task_id: index for index, task_id in enumerate(dag.order)}
    assert sorted(dag.order) == sorted(task.id for task in template.tasks)
    for task in dag.tasks:
        for dependency in task.depends_on:
            assert position[dependency] < position[task.id]


def test_the_dag_pins_the_template_key_and_version(template: Template) -> None:
    dag = compile_dag(template, hire())
    assert (dag.template_key, dag.template_version) == ("engineer-india", 1)


def test_the_compiled_task_keeps_the_template_task(template: Template) -> None:
    dag = compile_dag(template, hire())
    task = dag.task("welcome_message")
    assert task.task == template.task("welcome_message")
    assert task.task is not template.task("welcome_message")  # a copy: inputs are a plain dict


def test_changing_compiled_inputs_does_not_change_the_template(template: Template) -> None:
    dag = compile_dag(template, hire())
    dag.task("welcome_message").task.inputs["channel"] = "sms"
    original = template.task("welcome_message")
    assert original is not None
    assert original.inputs["channel"] == "telegram"


def test_compiling_does_not_change_its_inputs(template: Template) -> None:
    variables = hire(role="sales")
    before_vars, before_template = copy.deepcopy(variables), template.model_dump()
    compile_dag(template, variables)
    assert variables == before_vars
    assert template.model_dump() == before_template


def test_task_lookup_refuses_an_unknown_id(template: Template) -> None:
    with pytest.raises(KeyError):
        compile_dag(template, hire()).task("no_such_task")


def test_the_dag_document_is_json_with_every_task(template: Template) -> None:
    document = compile_dag(template, hire(role="sales")).document()
    assert document["template_key"] == "engineer-india"
    assert document["hash"].startswith("sha256:")
    skipped = next(task for task in document["tasks"] if task["id"] == "bitbucket_access")
    assert skipped["status"] == "skipped"
    assert skipped["skip_reason"]
    collect = next(task for task in document["tasks"] if task["id"] == "collect_docs")
    assert collect["due"] == "2026-11-24"


# --- box 3: compiling twice gives the same hash -----------------------------------------------------


def test_compiling_twice_gives_the_same_hash(template: Template) -> None:
    first = compile_dag(template, hire())
    second = compile_dag(Template.model_validate(seed_data()), dict(reversed(list(hire().items()))))
    assert first.hash == second.hash
    assert first == second
    assert first.hash.startswith("sha256:")
    assert len(first.hash) == len("sha256:") + 64


def test_the_hash_ignores_variables_the_template_does_not_declare(template: Template) -> None:
    assert compile_dag(template, hire()).hash == compile_dag(template, {**hire(), "notes": "x"}).hash


@pytest.mark.parametrize(
    ("change", "value"),
    [("role", "sales"), ("start_date", "2026-12-02")],
)
def test_a_different_compile_gives_a_different_hash(template: Template, change: str, value: str) -> None:
    assert compile_dag(template, hire()).hash != compile_dag(template, hire(**{change: value})).hash


def test_a_different_version_gives_a_different_hash() -> None:
    first = compile_dag(Template.model_validate(seed_data()), hire())
    second = compile_dag(Template.model_validate({**seed_data(), "version": 2}), hire())
    assert first.hash != second.hash


SEED_ENGINEER_HASH = "sha256:d814ca2bb552ca60a583bd1f50b196a9456a4f2ce2f2621a86b1407b55f2351e"


def test_the_hash_is_stable_across_runs(template: Template) -> None:
    """A pinned value: the hash may only change when the seed template or the compiled shape is changed
    on purpose. It does not depend on the process (hash seed, dict order)."""
    dag = compile_dag(template, hire())
    assert dag.hash == CompiledDag.hash_of(dag.document(include_hash=False))
    assert dag.hash == SEED_ENGINEER_HASH


def test_the_hash_is_the_same_in_another_process(template: Template) -> None:
    script = (
        "import yaml,sys\n"
        "from onb_contracts.templates import Template\n"
        "from onb_agents.dag import compile_dag\n"
        "d=yaml.safe_load(open(sys.argv[1])); d.update(version=1, status='published')\n"
        "v={'manager_id':'u_acme_mgr_ravi','start_date':'2026-12-01','location':'Bangalore','role':'engineer'}\n"
        "print(compile_dag(Template.model_validate(d), v).hash)\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": "12345"}
    result = subprocess.run([sys.executable, "-c", script, str(SEED_TEMPLATE)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == compile_dag(template, hire()).hash


def test_inputs_json_cannot_carry_fail_the_compile() -> None:
    data = seed_data()
    data["tasks"][0]["inputs"] = {"weight": float("nan")}
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire())
    assert caught.value.reason is CompileReason.BAD_INPUTS


# --- box 4: `sla: start_date - 7d` resolves to the right date ----------------------------------------


def test_sla_start_date_minus_7d_resolves_to_the_right_date(template: Template) -> None:
    dag = compile_dag(template, hire(start_date="2026-12-01"))
    assert dag.task("collect_docs").due == "2026-11-24"


@pytest.mark.parametrize(
    ("start", "due"),
    [
        ("2026-03-05", "2026-02-26"),  # across February
        ("2028-03-06", "2028-02-28"),  # a leap year
        ("2027-01-03", "2026-12-27"),  # across a year
    ],
)
def test_sla_offsets_cross_months_and_years(template: Template, start: str, due: str) -> None:
    assert compile_dag(template, hire(start_date=start)).task("collect_docs").due == due


def test_an_sla_without_offset_is_the_date_itself(template: Template) -> None:
    assert compile_dag(template, hire(start_date="2026-12-01")).task("orientation").due == "2026-12-01"


def test_a_date_object_is_accepted_for_a_date_variable(template: Template) -> None:
    dag = compile_dag(template, hire(start_date=date(2026, 12, 1)))
    assert dag.task("collect_docs").due == "2026-11-24"
    assert dag.hash == compile_dag(template, hire()).hash


def test_an_hour_offset_resolves_from_midnight_utc() -> None:
    data = seed_data()
    data["tasks"][0]["sla"] = {"due": "start_date + 36h"}
    dag = compile_dag(Template.model_validate(data), hire(start_date="2026-12-01"))
    assert dag.task("collect_docs").due == "2026-12-02T12:00:00+00:00"


def test_sla_hours_are_kept_for_the_runtime(template: Template) -> None:
    task = compile_dag(template, hire()).task("manager_approval")
    assert task.due is None
    assert task.task.sla_hours == 48


def test_a_task_without_an_sla_has_no_due_date(template: Template) -> None:
    assert compile_dag(template, hire()).task("bitbucket_access").due is None


# --- failing closed -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lifecycle",
    [{"status": "draft"}, {"status": "archived"}, {"version": None}],
)
def test_only_a_published_version_compiles(lifecycle: dict[str, Any]) -> None:
    template = Template.model_validate({**seed_data(), **lifecycle})
    with pytest.raises(CompileError) as caught:
        compile_dag(template, hire())
    assert caught.value.reason is CompileReason.NOT_PUBLISHED


@pytest.mark.parametrize("missing", ["role", "start_date"])
def test_a_missing_variable_fails_the_compile(template: Template, missing: str) -> None:
    variables = hire()
    del variables[missing]
    with pytest.raises(CompileError) as caught:
        compile_dag(template, variables)
    assert caught.value.reason is CompileReason.MISSING_VARIABLE
    assert missing in str(caught.value)


def test_a_variable_nothing_reads_may_be_missing(template: Template) -> None:
    variables = hire()
    del variables["location"]
    assert compile_dag(template, variables).task("laptop").status == "pending"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("role", "astronaut"),  # outside the enum
        ("role", 3),
        ("start_date", "01/12/2026"),
        ("start_date", "2026-02-30"),
        ("start_date", "2026-12-01T10:00:00"),
        ("start_date", datetime(2026, 12, 1, 10, 0)),  # a datetime is not a date
        ("start_date", "\uff12\uff10\uff12\uff16-12-01"),  # full-width (non-ASCII) digits
        ("location", 7),
        ("manager_id", None),
    ],
)
def test_a_variable_of_the_wrong_type_fails_the_compile(template: Template, name: str, value: object) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(template, hire(**{name: value}))
    assert caught.value.reason is CompileReason.BAD_VARIABLE
    assert name in str(caught.value)
    assert str(value) not in str(caught.value)  # never echo the value


@pytest.mark.parametrize(
    ("variable", "good", "bad"),
    [
        ({"name": "remote", "type": "boolean"}, True, 1),
        ({"name": "level", "type": "number"}, 3, True),
        ({"name": "level", "type": "number"}, 2.5, float("nan")),
    ],
)
def test_boolean_and_number_variables_are_strict(variable: dict[str, Any], good: object, bad: object) -> None:
    data = seed_data()
    data["variables"] = [*data["variables"], variable]
    template = Template.model_validate(data)
    compile_dag(template, hire(**{variable["name"]: good}))
    with pytest.raises(CompileError) as caught:
        compile_dag(template, hire(**{variable["name"]: bad}))
    assert caught.value.reason is CompileReason.BAD_VARIABLE


def test_a_condition_that_cannot_be_decided_fails_the_compile() -> None:
    data = seed_data()
    data["variables"] = [*data["variables"], {"name": "level", "type": "number"}]
    data["tasks"][3]["when"] = "level > 'senior'"  # ordering across types: undecidable, never false
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire(level=3))
    assert caught.value.reason is CompileReason.BAD_CONDITION
    assert caught.value.task_id == "bitbucket_access"


def test_an_unparseable_condition_fails_the_compile() -> None:
    data = seed_data()
    data["tasks"][3]["when"] = "len(role) > 3"
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire())
    assert caught.value.reason is CompileReason.BAD_CONDITION


def test_a_cycle_fails_the_compile() -> None:
    data = seed_data()
    data["tasks"][0]["depends_on"] = ["laptop"]
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire())
    assert caught.value.reason is CompileReason.CYCLE


def test_an_unknown_dependency_fails_the_compile() -> None:
    data = seed_data()
    data["tasks"][0]["depends_on"] = ["ghost"]
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire())
    assert caught.value.reason is CompileReason.UNKNOWN_DEPENDENCY
    assert caught.value.task_id == "collect_docs"


@pytest.mark.parametrize("variable", [None, {"name": "start_date", "type": "string"}])
def test_an_sla_that_reads_no_date_variable_fails_the_compile(variable: dict[str, str] | None) -> None:
    data = seed_data()
    others = [v for v in data["variables"] if v["name"] != "start_date"]
    data["variables"] = others + ([variable] if variable else [])
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire())
    assert caught.value.reason is CompileReason.BAD_SLA
    assert caught.value.task_id == "collect_docs"


def test_a_due_date_outside_the_calendar_fails_the_compile() -> None:
    data = seed_data()
    data["tasks"][0]["sla"] = {"due": "start_date - 9999d"}
    with pytest.raises(CompileError) as caught:
        compile_dag(Template.model_validate(data), hire(start_date="0001-01-02"))
    assert caught.value.reason is CompileReason.BAD_SLA


def test_variables_must_be_a_mapping(template: Template) -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(template, [("role", "engineer")])  # type: ignore[arg-type]  # the guard under test
    assert caught.value.reason is CompileReason.BAD_VARIABLE


def test_a_template_must_be_a_template() -> None:
    with pytest.raises(CompileError) as caught:
        compile_dag(seed_data(), hire())  # type: ignore[arg-type]  # the guard under test
    assert caught.value.reason is CompileReason.NOT_PUBLISHED
