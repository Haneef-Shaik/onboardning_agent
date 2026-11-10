"""The runner (evals/README.md "How a case runs"): fresh fixture per repetition, setup, the agent through
the `fake` connector, capture, checks; spans carry `eval.run_id` and `eval.case_id`."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from onb_data import DataStore
from onb_evals.cases import EvalCase, load_cases
from onb_evals.metrics import CaseResult
from onb_evals.runner import AgentInvocation, EvalRunner
from onb_evals.scripted import ScriptedAgent, ScriptedLLM, fake_runtime
from onb_evals.spans import EVAL_CASE_ID, EVAL_RUN_ID, EvalSpans
from onb_telemetry.testing import captured_telemetry

from .memory_store import MemoryStore, as_data_store
from .support import DATASET, SEED

CASES = {case.id: case for case in load_cases(DATASET)}


class MemoryDatabases:
    def __init__(self) -> None:
        self.stores: list[MemoryStore] = []

    @asynccontextmanager
    async def fresh(self) -> AsyncIterator[DataStore]:
        store = MemoryStore()
        self.stores.append(store)
        try:
            yield as_data_store(store)
        finally:
            await store.close()


def _final(output: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(output)}


SCRIPTS: dict[str, list[dict[str, Any]]] = {
    "db-001": [
        {
            "tool_calls": [
                {
                    "name": "update_one",
                    "args": {
                        "collection": "run_tasks",
                        "filter": {"run_id": "run_acme_003", "task_id": "schedule_orientation"},
                        "set": {"status": "done", "note": "Orientation booked for 8 Dec, 10:00"},
                    },
                }
            ]
        },
        _final({"status": "ok", "answer": {"updated": True}}),
    ],
    "db-002": [
        {"tool_calls": [{"name": "find", "args": {"collection": "onboarding_runs", "filter": {"status": "blocked"}}}]},
        _final({"status": "ok", "answer": {"run_ids": ["run_acme_002"]}}),
    ],
    "db-004": [
        {
            "tool_calls": [
                {"name": "find", "args": {"collection": "new_hires", "filter": {"$where": "sleep(5000) || true"}}}
            ]
        },
        _final({"status": "refused", "answer": {"reason": "unsafe query"}}),
    ],
    "db-005": [_final({"status": "refused", "answer": {"reason": "PII is changed by HR only"}})],
    "tp-002": [
        _final(
            {
                "status": "ok",
                "message": {"channel": "telegram", "recipient_token": "ptk_seed_acme001_mobile", "body": "Hi"},
            }
        )
    ],
}


def _runner(
    databases: MemoryDatabases, spans: EvalSpans | None = None, scripts: dict[str, Any] = SCRIPTS
) -> EvalRunner:
    runtime = fake_runtime()
    return EvalRunner(
        databases=databases,
        seed=SEED,
        adapter_for=lambda case: ScriptedAgent(),
        llm_for=lambda case: ScriptedLLM(runtime, scripts[case.id]),
        spans=spans,
    )


def _run(case: EvalCase, *, repeat: int = 3, **kwargs: Any) -> tuple[CaseResult, MemoryDatabases]:
    databases = MemoryDatabases()
    runner = _runner(databases, **kwargs)
    results = asyncio.run(runner.run([case], repeat=repeat, run_id="run_test"))
    return results[0], databases


def test_a_scripted_case_passes_every_check_in_every_repetition() -> None:
    result, databases = _run(CASES["db-002"])
    assert [r.passed for r in result.repetitions] == [True, True, True]
    assert all(c.passed for r in result.repetitions for c in r.checks)
    assert len(databases.stores) == 3
    assert all(store.closed for store in databases.stores)


def test_every_repetition_starts_from_a_fresh_fixture() -> None:
    result, databases = _run(CASES["db-001"])
    assert all(r.passed for r in result.repetitions), [c for r in result.repetitions for c in r.checks]
    assert len({id(store) for store in databases.stores}) == 3


def test_capture_records_steps_tokens_latency_and_tool_call_accuracy() -> None:
    result, _ = _run(CASES["db-002"], repeat=1)
    rep = result.repetitions[0]
    assert rep.steps == 2
    assert rep.input_tokens > 0
    assert rep.output_tokens > 0
    assert rep.latency_ms >= 0
    assert (rep.tool_calls_ok, rep.tool_calls_checked) == (1, 1)


def test_a_denied_call_is_captured_and_the_state_checks_still_run() -> None:
    result, databases = _run(CASES["db-004"], repeat=1)
    rep = result.repetitions[0]
    assert rep.passed, rep.checks
    audit = databases.stores[0].data["audit_log"]
    assert [entry["outcome"] for entry in audit] == ["denied"]


def test_free_text_input_reaches_the_model_redacted_and_the_check_sees_it() -> None:
    result, _ = _run(CASES["db-005"], repeat=1)
    assert result.repetitions[0].passed, result.repetitions[0].checks


def test_seed_tokens_in_the_input_and_script_are_issued_tokens_inside_and_seed_names_to_checks() -> None:
    result, databases = _run(CASES["tp-002"], repeat=1)
    assert result.repetitions[0].passed, result.repetitions[0].checks
    assert "ptk_seed_" not in databases.stores[0].dump()


def test_an_agent_that_raises_fails_the_repetition_with_the_error_recorded() -> None:
    broken = {**SCRIPTS, "db-002": [{"content": "not json"}]}
    result, _ = _run(CASES["db-002"], repeat=1, scripts=broken)
    rep = result.repetitions[0]
    assert not rep.passed
    assert rep.error is not None
    assert "JSON" in rep.error


def test_an_agent_that_never_stops_calling_tools_is_cut_off() -> None:
    loop = [{"tool_calls": [{"name": "count", "args": {"collection": "new_hires", "filter": {}}}]}] * 20
    result, _ = _run(CASES["db-002"], repeat=1, scripts={**SCRIPTS, "db-002": loop})
    assert "turns" in (result.repetitions[0].error or "")


def test_every_span_of_a_case_carries_the_run_and_case_ids() -> None:
    spans = EvalSpans()
    with captured_telemetry(processors=[spans]) as telemetry:
        result, _ = _run(CASES["db-002"], repeat=1, spans=spans)
        exported = telemetry.spans()
    assert result.repetitions[0].passed
    assert exported
    assert {(span.attributes or {}).get(EVAL_RUN_ID) for span in exported} == {"run_test"}
    assert {(span.attributes or {}).get(EVAL_CASE_ID) for span in exported} == {"db-002"}
    names = {span.name for span in exported}
    assert {"eval.case", "llm.chat", "eval.tool"} <= names


def test_the_trace_scope_reads_the_cases_spans() -> None:
    spans = EvalSpans()
    with captured_telemetry(processors=[spans]):
        databases = MemoryDatabases()
        runner = _runner(databases, spans=spans)
        capture = asyncio.run(runner.capture_once(CASES["db-002"], 0, "run_test"))
    assert any("eval.tool" in text and "find" in text for text in capture.trace)


def test_an_invocation_carries_the_case_and_the_input_with_issued_tokens() -> None:
    seen: list[AgentInvocation] = []

    class Spy(ScriptedAgent):
        async def invoke(self, invocation: AgentInvocation) -> dict[str, Any]:
            seen.append(invocation)
            return await super().invoke(invocation)

    databases = MemoryDatabases()
    runtime = fake_runtime()
    runner = EvalRunner(
        databases=databases,
        seed=SEED,
        adapter_for=lambda c: Spy(),
        llm_for=lambda c: ScriptedLLM(runtime, SCRIPTS["tp-002"]),
    )
    asyncio.run(runner.run([CASES["tp-002"]], repeat=1, run_id="r"))
    note = seen[0].input["untrusted_note"]
    assert "ptk_seed_" not in note
    assert seen[0].case.id == "tp-002"


def test_repeat_must_be_positive() -> None:
    with pytest.raises(ValueError, match="repeat"):
        asyncio.run(_runner(MemoryDatabases()).run([CASES["db-002"]], repeat=0, run_id="r"))
