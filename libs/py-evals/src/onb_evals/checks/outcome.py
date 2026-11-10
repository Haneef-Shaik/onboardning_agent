"""Outcome checks: exact, set and substring matches on structured output fields (PRD §8.2 "Outcome")."""

from __future__ import annotations

import re
from typing import Any

from onb_evals.capture import to_text
from onb_evals.checks import params as p
from onb_evals.checks.base import CheckResult, failed, passed, register
from onb_evals.context import CheckContext
from onb_evals.paths import MISSING, resolve
from onb_evals.values import json_equal

_SHOWN = 80  # characters of a value echoed in a failure detail


def _show(value: Any) -> str:
    text = "MISSING" if value is MISSING else to_text(value)
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "…"


def _at(context: CheckContext, path: str) -> Any:
    return resolve(context.output, path)


@register("status_in")
async def status_in(params: p.StatusIn, context: CheckContext) -> CheckResult:
    status = _at(context, "status")
    if isinstance(status, str) and status in params.values:
        return passed()
    return failed(f"status is {_show(status)}, expected one of {params.values}")


@register("field_equals")
async def field_equals(params: p.FieldEquals, context: CheckContext) -> CheckResult:
    value = _at(context, params.path)
    if value is not MISSING and json_equal(value, params.value):
        return passed()
    return failed(f"{params.path} is {_show(value)}, expected {_show(params.value)}")


@register("field_in")
async def field_in(params: p.FieldIn, context: CheckContext) -> CheckResult:
    value = _at(context, params.path)
    if value is not MISSING and any(json_equal(value, option) for option in params.values):
        return passed()
    return failed(f"{params.path} is {_show(value)}, expected one of {_show(params.values)}")


def _same(item: Any, wanted: Any, case_insensitive: bool) -> bool:
    if case_insensitive and isinstance(item, str) and isinstance(wanted, str):
        return item.casefold() == wanted.casefold()
    return json_equal(item, wanted)


def _contains(container: Any, wanted: Any, case_insensitive: bool) -> bool:
    """An array has an element equal to `wanted`, or a string has `wanted` as a substring (README)."""
    if isinstance(container, str):
        if not isinstance(wanted, str):
            return False
        return wanted.casefold() in container.casefold() if case_insensitive else wanted in container
    if isinstance(container, list):
        return any(_same(item, wanted, case_insensitive) for item in container)
    return False


@register("field_contains")
async def field_contains(params: p.FieldContains, context: CheckContext) -> CheckResult:
    value = _at(context, params.path)
    if _contains(value, params.value, params.case_insensitive):
        return passed()
    return failed(f"{params.path} ({_show(value)}) does not contain {_show(params.value)}")


@register("field_empty")
async def field_empty(params: p.FieldEmpty, context: CheckContext) -> CheckResult:
    value = _at(context, params.path)
    if value is None or (isinstance(value, list | str) and len(value) == 0):
        return passed()
    return failed(f'{params.path} is {_show(value)}, expected [], "" or null')


def _text_matches(context: CheckContext, params: p.ContainsValues, *, need_all: bool) -> CheckResult:
    value = _at(context, params.path)
    if not isinstance(value, str):
        return failed(f"{params.path} is {_show(value)}, not a string")
    found = [v for v in params.values if v.casefold() in value.casefold()]
    ok = len(found) == len(params.values) if need_all else bool(found)
    if ok:
        return passed()
    missing = [v for v in params.values if v not in found]
    return failed(f"{params.path} lacks {'all of' if need_all else 'any of'} {missing}")


@register("contains_all")
async def contains_all(params: p.ContainsValues, context: CheckContext) -> CheckResult:
    return _text_matches(context, params, need_all=True)


@register("contains_any")
async def contains_any(params: p.ContainsValues, context: CheckContext) -> CheckResult:
    return _text_matches(context, params, need_all=False)


@register("max_length")
async def max_length(params: p.MaxLength, context: CheckContext) -> CheckResult:
    value = _at(context, params.path)
    if isinstance(value, str) and len(value) <= params.value:
        return passed()
    size = len(value) if isinstance(value, str) else _show(value)
    return failed(f"{params.path} length is {size}, limit {params.value}")


@register("regex_absent")
async def regex_absent(params: p.RegexAbsent, context: CheckContext) -> CheckResult:
    """Default scope: the whole serialised output and every tool call's args (README)."""
    if params.path is not None:
        value = _at(context, params.path)
        texts: tuple[str, ...] = () if value is MISSING else (to_text(value),)
        where = params.path
    else:
        texts = context.capture.scope_texts("output") + context.capture.scope_texts("tool_args")
        where = "output and tool args"
    pattern = re.compile(params.pattern)
    if any(pattern.search(text) for text in texts):
        return failed(f"pattern {params.pattern!r} found in {where}")
    return passed()


@register("citation_includes")
async def citation_includes(params: p.CitationIncludes, context: CheckContext) -> CheckResult:
    citations = _at(context, "citations")
    if isinstance(citations, list) and any(
        isinstance(c, dict) and c.get("run_id") == params.run_id and c.get("task_id") == params.task_id
        for c in citations
    ):
        return passed()
    return failed(f"citations do not include {params.run_id}/{params.task_id}")
