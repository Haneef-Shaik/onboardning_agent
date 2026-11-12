"""RUN-3 boxes 1-3: a task node runs its registered agent with a per-task agent token, a PII grant only
when the template task declares `pii_access`, the model recorded on the task, and a per-run token budget.

The run is started with the real `RunStarter` and driven by the real `start_run_handler`; the agent
runtime is AG-1's `AgentRunner` with LLM-1's `fake` connector. No real model is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from onb_agents.runs import RunState, TaskState
from onb_agents.runtime import AgentTask, task_scopes
from onb_agents.tokens import verify_agent_token
from onb_crypto import VaultField
from onb_orchestrator.agent_executor import AgentTaskExecutor, AgentTaskFailedError
from onb_orchestrator.executor import TaskWork

from .agent_support import (
    AGENT_TOKEN_KEY,
    HIRE,
    MANIFESTS,
    OK,
    ORG,
    Agents,
    Harness,
    Models,
    Tools,
    final,
    task_doc,
    template,
)

PII = {"fields": ["mobile"], "sink": "notify.telegram"}


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def harness(tmp_path: Path, script: list[dict[str, Any]] | None = None, **kw: Any) -> Harness:
    return Harness.create(Models(script or [final(OK)], tmp_path), **kw)


def started(h: Harness, *tasks: dict[str, Any]) -> str:
    return run(h.start(template(*tasks)))


# --- box 1: a grant only when the template task declares pii_access ---------------------------------


def test_a_task_without_pii_access_gets_no_grant(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("plain"))
    run(h.execute(run_id, h.executor()))
    assert run(h.task(run_id, "plain"))["status"] == "done"
    [(agent_task, _)] = h.tools.for_task("plain")
    assert agent_task.grant is None
    assert h.issuer.issued == 0


def test_a_task_with_pii_access_gets_a_grant_limited_to_its_fields_and_sink(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access=PII, agent="db_agent"))
    run(h.execute(run_id, h.executor()))
    assert run(h.task(run_id, "notify_hire"))["status"] == "done"
    [(agent_task, _)] = h.tools.for_task("notify_hire")
    assert agent_task.grant is not None
    claims = h.verifier().verify(agent_task.grant)
    assert claims.fields == frozenset({VaultField.MOBILE})  # exactly the declared fields, nothing else
    assert claims.sink == "notify.telegram"
    assert (claims.org_id, claims.run_id, claims.task_id, claims.subject_id) == (ORG, run_id, "notify_hire", HIRE)
    assert h.issuer.issued == 1


def test_the_grant_is_issued_with_the_runs_own_run_id(tmp_path: Path) -> None:
    """NOTIF-1: the sink refuses a grant whose run is not the hub-signed call context's (`wrong_run`)."""
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access=PII))
    run(h.execute(run_id, h.executor()))
    [(agent_task, token)] = h.tools.for_task("notify_hire")
    assert agent_task.grant is not None
    assert h.verifier().verify(agent_task.grant).run_id == run_id == agent_task.run_id
    assert verify_agent_token(token, AGENT_TOKEN_KEY).claims.run_id == run_id


def test_only_the_task_that_declares_pii_access_gets_a_grant_in_a_run(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(
        h, task_doc("before"), task_doc("notify_hire", "before", pii_access=PII), task_doc("after", "notify_hire")
    )
    run(h.execute(run_id, h.executor()))
    grants = {t.task_id: t.grant is not None for t, _ in h.tools.opened}
    assert grants == {"before": False, "notify_hire": True, "after": False}
    assert h.issuer.issued == 1


def test_each_task_gets_its_own_agent_token_scoped_to_its_agent(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("one"), task_doc("two", "one"))
    run(h.execute(run_id, h.executor()))
    verified = {t.task_id: verify_agent_token(token, AGENT_TOKEN_KEY) for t, token in h.tools.opened}
    assert set(verified) == {"one", "two"}
    assert verified["one"].jti != verified["two"].jti
    for item in verified.values():
        assert (item.claims.agent, item.claims.run_id, item.claims.org_id) == ("db_agent", run_id, ORG)
        assert item.claims.scopes == task_scopes(MANIFESTS["db_agent"], ())


def test_the_grant_and_the_token_are_never_stored(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access=PII))
    run(h.execute(run_id, h.executor()))
    [(agent_task, token)] = h.tools.opened
    stored = h.everything_stored()
    assert agent_task.grant is not None
    assert agent_task.grant not in stored
    assert token not in stored


def test_a_field_the_sink_does_not_resolve_fails_the_task_before_the_agent_runs(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access={"fields": ["aadhaar"], "sink": "notify.telegram"}))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "notify_hire"))
    assert record["status"] == "failed"
    assert record["failure"] == {"kind": "grant_refused", "reason": "SinkPolicyError"}
    assert h.tools.opened == []  # the agent never started
    assert h.issuer.issued == 0
    assert run(h.run(run_id))["status"] == RunState.FAILED.value


def test_a_field_that_is_not_a_vault_field_fails_the_task(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access={"fields": ["shoe_size"], "sink": "notify.telegram"}))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "notify_hire"))
    assert record["status"] == "failed"
    assert record["failure"]["kind"] == "grant_refused"
    assert h.tools.opened == []


def test_pii_access_without_a_grant_issuer_fails_closed(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("notify_hire", pii_access=PII))
    run(h.execute(run_id, h.executor(grants=None)))
    record = run(h.task(run_id, "notify_hire"))
    assert record["status"] == "failed"
    assert record["failure"] == {"kind": "grant_refused", "reason": "no_grant_issuer"}
    assert h.tools.opened == []


# --- the agent's outcome, visible on the task -------------------------------------------------------


def test_a_completed_agent_ends_the_task_done_with_its_result_stored(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("plain"))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "plain"))
    assert record["status"] == TaskState.DONE.value
    assert record["result"]["outcome"] == "completed"
    assert record["result"]["output"] == OK
    assert run(h.run(run_id))["status"] == RunState.COMPLETED.value


def test_a_task_naming_an_unregistered_or_retired_agent_fails(tmp_path: Path) -> None:
    for agents, kind in (
        (Agents(missing=frozenset({"db_agent"})), "no_agent"),
        (Agents(retired=frozenset({"db_agent"})), "retired_agent"),
    ):
        h = harness(tmp_path, agents=agents)
        run_id = started(h, task_doc("plain"))
        run(h.execute(run_id, h.executor()))
        record = run(h.task(run_id, "plain"))
        assert record["status"] == "failed"
        assert record["failure"] == {"kind": kind, "reason": "db_agent"}
        assert h.tools.opened == []


def test_a_failed_agent_fails_the_task_and_the_run_with_the_reason(tmp_path: Path) -> None:
    h = harness(tmp_path, [{"content": "not json at all"}, {"content": "still not json"}])
    run_id = started(h, task_doc("plain"))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "plain"))
    assert record["status"] == "failed"
    assert record["failure"]["kind"] == "invalid_output"
    assert record["result"]["outcome"] == "failed"
    assert run(h.run(run_id))["status"] == RunState.FAILED.value


# --- box 2: the model is recorded on the task and does not change mid-run ---------------------------


def test_the_model_is_recorded_on_the_task(tmp_path: Path) -> None:
    h = harness(tmp_path)
    run_id = started(h, task_doc("plain"))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "plain"))
    assert record["llm"] == {"connector": "fake", "model": "fake-standard", "alias": "standard", "source": "manifest"}
    assert record["result"]["llm"] == record["llm"]


def test_a_config_change_while_the_task_runs_does_not_change_its_recorded_model(tmp_path: Path) -> None:
    models = Models([final(OK)], tmp_path)
    h = Harness.create(models, Tools(on_open=lambda _t: models.reconfigure("fake-standard-v2")))
    run_id = started(h, task_doc("plain"))
    run(h.execute(run_id, h.executor()))
    record = run(h.task(run_id, "plain"))
    assert record["llm"]["model"] == "fake-standard"
    assert models.runtime.config.aliases["standard"].model == "fake-standard-v2"  # the config did change


def test_a_new_attempt_of_a_task_keeps_its_model_and_a_new_task_takes_the_new_config(tmp_path: Path) -> None:
    models = Models([final(OK)], tmp_path)
    h = Harness.create(models)
    run_id = started(h, task_doc("first"), task_doc("second"))
    executor = h.executor()
    run(h.execute(run_id, executor))
    first = run(h.task(run_id, "first"))["llm"]
    assert first["model"] == "fake-standard"
    models.reconfigure("fake-standard-v2")
    resolved_before = len(models.chosen)
    work = TaskWork(run_id=run_id, task_id="first", agent="db_agent", action="do_first", type="action", attempt=2)

    async def again() -> None:
        from onb_data import tenant_scope

        with tenant_scope(ORG):
            await executor.execute(work)

    run(again())
    assert run(h.task(run_id, "first"))["llm"] == first  # recorded once, never rewritten
    assert models.pinned_from == [first]  # the second attempt rebuilt the recorded model, not the default
    assert len(models.chosen) == resolved_before  # and never resolved the configuration again
    # A task that starts after the change is a new task: it takes the new configuration.
    fresh = started(h, task_doc("third"))
    run(h.execute(fresh, executor))
    assert run(h.task(fresh, "third"))["llm"]["model"] == "fake-standard-v2"


# --- box 3: an exhausted per-run token budget fails the task visibly --------------------------------


def test_an_exhausted_run_token_budget_fails_the_task_visibly(tmp_path: Path) -> None:
    h = harness(tmp_path, [final(OK, tokens=600)])
    run_id = started(h, task_doc("one"), task_doc("two", "one"), task_doc("three", "two"))
    run(h.execute(run_id, h.executor(run_max_tokens=1000)))
    one, two = run(h.task(run_id, "one")), run(h.task(run_id, "two"))
    assert one["status"] == "done"  # 600 of 1000
    assert two["status"] == "failed"  # 1200 > 1000
    assert two["failure"] == {"kind": "budget_exceeded", "reason": "run tokens 1200/1000"}
    assert two["result"]["outcome"] == "failed"
    assert run(h.task(run_id, "three"))["status"] == "cancelled"
    assert run(h.run(run_id))["status"] == RunState.FAILED.value


def test_the_run_budget_counts_tokens_across_parallel_tasks(tmp_path: Path) -> None:
    h = harness(tmp_path, [final(OK, tokens=600)])
    run_id = started(h, task_doc("one"), task_doc("two"))
    run(h.execute(run_id, h.executor(run_max_tokens=1000)))
    statuses = sorted(run(h.task(run_id, t))["status"] for t in ("one", "two"))
    assert statuses.count("failed") >= 1  # 600 + 600 tokens cannot both fit in 1000
    assert run(h.run(run_id))["status"] == RunState.FAILED.value


def test_the_run_budget_is_rebuilt_from_the_tasks_recorded_usage_after_a_restart(tmp_path: Path) -> None:
    h = harness(tmp_path, [final(OK, tokens=600)])
    run_id = started(h, task_doc("one"), task_doc("two", "one"))
    run(h.execute(run_id, h.executor(run_max_tokens=10_000)))  # both fit: 1200 tokens recorded in all
    assert run(h.run(run_id))["status"] == RunState.COMPLETED.value
    # A restarted worker (a new executor, empty memory) with a 1000-token run budget reads the 1200 recorded
    # tokens, so the very next task of the run is refused before its first model call.
    restarted = h.executor(run_max_tokens=1000)
    models_built = len(h.models.pinned_from)

    async def retry() -> None:
        from onb_data import tenant_scope

        with tenant_scope(ORG):
            await restarted.execute(
                TaskWork(run_id=run_id, task_id="two", agent="db_agent", action="do_two", type="action", attempt=2)
            )

    with pytest.raises(AgentTaskFailedError) as refused:
        run(retry())
    assert (refused.value.kind, refused.value.reason) == ("budget_exceeded", "run tokens 1200/1000")
    assert len(h.models.pinned_from) == models_built + 1  # it got as far as the agent, which refused it


def test_a_task_that_fits_the_run_budget_records_its_usage(tmp_path: Path) -> None:
    h = harness(tmp_path, [final(OK, tokens=600)])
    run_id = started(h, task_doc("one"))
    run(h.execute(run_id, h.executor(run_max_tokens=1000)))
    assert run(h.task(run_id, "one"))["result"]["usage"] == {"steps": 1, "tokens": 600}


def test_agent_task_is_constructed_from_the_stored_task(tmp_path: Path) -> None:
    """The executor hands the runtime ids and the template's inputs, never a hire value."""
    h = harness(tmp_path)
    run_id = started(h, task_doc("plain", inputs={"channel": "telegram"}))
    run(h.execute(run_id, h.executor()))
    [(agent_task, _)] = h.tools.for_task("plain")
    assert isinstance(agent_task, AgentTask)
    assert agent_task.input["inputs"] == {"channel": "telegram"}
    assert agent_task.input["action"] == "do_plain"
    assert agent_task.input["hire_id"] == HIRE
    assert (agent_task.org_id, agent_task.run_id, agent_task.task_id) == (ORG, run_id, "plain")


def test_the_executor_is_an_agent_task_executor() -> None:
    assert AgentTaskExecutor.__name__ == "AgentTaskExecutor"
