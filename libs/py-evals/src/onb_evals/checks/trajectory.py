"""Trajectory and state checks (PRD §8.2 "Trajectory", "State", "Cost")."""

from __future__ import annotations

from typing import Any

from onb_evals.capture import ToolCall, ToolOutcome
from onb_evals.checks import params as p
from onb_evals.checks.base import CheckResult, failed, passed, register
from onb_evals.context import CheckContext
from onb_evals.paths import resolve
from onb_evals.values import json_equal, subset_match


def _matching(calls: tuple[ToolCall, ...], params: p.ToolCalled | p.ToolNotCalled) -> list[ToolCall]:
    names = params.tool_names()
    return [call for call in calls if call.name in names and subset_match(call.args, params.args_subset)]


@register("tool_called")
async def tool_called(params: p.ToolCalled, context: CheckContext) -> CheckResult:
    """A matching call that the hub let through; a denied attempt does not do the job."""
    matches = _matching(context.capture.tool_calls, params)
    if any(call.outcome is ToolOutcome.ALLOWED for call in matches):
        return passed()
    if matches:
        return failed(f"{sorted(params.tool_names())} was attempted but never allowed")
    return failed(f"no call to {sorted(params.tool_names())} matching {params.args_subset}")


@register("tool_not_called")
async def tool_not_called(params: p.ToolNotCalled, context: CheckContext) -> CheckResult:
    """Denied attempts still count (README)."""
    matches = _matching(context.capture.tool_calls, params)
    if matches:
        outcomes = sorted({call.outcome.value for call in matches})
        return failed(f"{len(matches)} call(s) to {sorted(params.tool_names())} ({', '.join(outcomes)})")
    return passed()


@register("max_steps")
async def max_steps(params: p.MaxSteps, context: CheckContext) -> CheckResult:
    steps = context.capture.steps
    return passed() if steps <= params.value else failed(f"{steps} steps > {params.value}")


def _org_for(params: p.FinalState, context: CheckContext) -> str:
    """The filter's own `org_id` when it names one, else the case's org."""
    named = params.filter.get("org_id")
    return named if isinstance(named, str) else context.case.actor.org_id


def _mismatches(document: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    return [key for key, value in expect.items() if not json_equal(resolve(document, key), value)]


@register("final_state")
async def final_state(params: p.FinalState, context: CheckContext) -> CheckResult:
    query = {k: v for k, v in params.filter.items() if k != "org_id"}
    documents = await context.state.find(params.collection, query, org_id=_org_for(params, context))
    count = len(documents)
    if params.expect_count is not None:
        ok = count == params.expect_count
        return passed() if ok else failed(f"{params.collection}: {count} documents, expected {params.expect_count}")
    if params.expect_count_gte is not None:
        ok = count >= params.expect_count_gte
        return (
            passed() if ok else failed(f"{params.collection}: {count} documents, expected ≥ {params.expect_count_gte}")
        )
    if count == 0:
        return failed(f"{params.collection}: no document matches the filter")
    expect = params.expect or {}
    wrong = sorted({key for document in documents for key in _mismatches(document, expect)})
    return failed(f"{params.collection}: {wrong} differ from the expected state") if wrong else passed()
