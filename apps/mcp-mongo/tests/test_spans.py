"""Every call's span: `db.operation`, `db.collection`, `agent.name`, `org_id` and a result count,
never document content (PRD §7.5, §7.7). In-memory exporter."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import ClientSession
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry.sdk.trace import ReadableSpan

from .fakes import FakeDatabase
from .harness import call

CONTENT = "Zq7-document-content-marker"
SPAN_PREFIX = "mcp.mongo."


def _seed(db: FakeDatabase) -> None:
    db["new_hires"].docs.extend(
        [
            {"_id": 1, "org_id": "org_a", "note": CONTENT, "status": "open"},
            {"_id": 2, "org_id": "org_a", "note": CONTENT, "status": "open"},
            {"_id": 3, "org_id": "org_b", "note": CONTENT, "status": "open"},
        ]
    )
    db["run_tasks"].docs.append({"_id": 9, "org_id": "org_a", "state": "todo"})


CALLS: list[tuple[str, dict[str, Any], str, int]] = [
    ("list_collections", {}, "*", 2),
    ("find", {"collection": "new_hires", "filter": {"status": "open"}, "limit": 50}, "new_hires", 2),
    ("count", {"collection": "new_hires", "filter": {"status": "open"}}, "new_hires", 2),
    ("aggregate", {"collection": "new_hires", "pipeline": [{"$match": {"status": "open"}}]}, "new_hires", 2),
    ("insert_one", {"collection": "run_tasks", "document": {"note": CONTENT}}, "run_tasks", 1),
    (
        "update_one",
        {"collection": "run_tasks", "filter": {"_id": 9}, "update": {"$set": {"note": CONTENT}}},
        "run_tasks",
        1,
    ),
]


def _tool_spans(telemetry: CapturedTelemetry) -> list[ReadableSpan]:
    return [span for span in telemetry.spans() if span.name.startswith(SPAN_PREFIX)]


def _all_text(span: ReadableSpan) -> str:
    parts = [repr(dict(span.attributes or {}))]
    parts += [f"{event.name} {dict(event.attributes or {})!r}" for event in span.events]
    parts.append(span.status.description or "")
    return " ".join(parts)


@pytest.mark.parametrize(("tool", "args", "collection", "count"), CALLS, ids=[c[0] for c in CALLS])
def test_each_call_has_one_span_with_the_required_attributes(
    db: FakeDatabase, telemetry: CapturedTelemetry, tool: str, args: dict[str, Any], collection: str, count: int
) -> None:
    _seed(db)

    async def fn(session: ClientSession) -> Any:
        return await session.call_tool(tool, args)

    result = call(db, fn)
    assert not result.isError
    spans = _tool_spans(telemetry)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert spans[0].name == SPAN_PREFIX + tool
    assert attrs["db.operation"] == tool
    assert attrs["db.collection"] == collection
    assert attrs["agent.name"] == "db_agent"
    assert attrs["org_id"] == "org_a"
    assert attrs["run_id"] == "run_1"
    assert attrs["db.result_count"] == count
    assert attrs["outcome"] == "ok"


@pytest.mark.parametrize(("tool", "args", "collection", "count"), CALLS, ids=[c[0] for c in CALLS])
def test_no_span_carries_document_content(
    db: FakeDatabase, telemetry: CapturedTelemetry, tool: str, args: dict[str, Any], collection: str, count: int
) -> None:
    _seed(db)

    async def fn(session: ClientSession) -> Any:
        return await session.call_tool(tool, args)

    call(db, fn)
    spans = telemetry.spans()
    assert spans, "expected spans"
    for span in spans:
        text = _all_text(span)
        assert CONTENT not in text, span.name
        assert "open" not in text.replace("outcome", ""), span.name  # filter values stay out too


def test_a_rejected_call_has_a_denied_span_with_a_reason(db: FakeDatabase, telemetry: CapturedTelemetry) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.call_tool("find", {"collection": "new_hires", "filter": {"org_id": "org_b"}, "limit": 5})

    call(db, fn)
    (span,) = _tool_spans(telemetry)
    attrs = dict(span.attributes or {})
    assert attrs["outcome"] == "denied"
    assert attrs["denial.reason"] == "org_id_not_allowed"
    assert attrs["org_id"] == "org_a"
    assert attrs["db.collection"] == "new_hires"
    assert attrs["db.result_count"] == 0
    assert "org_b" not in _all_text(span)


def test_an_invalid_argument_call_has_a_denied_span(db: FakeDatabase, telemetry: CapturedTelemetry) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.call_tool("find", {"collection": "new_hires", "limit": 500})

    call(db, fn)
    (span,) = _tool_spans(telemetry)
    attrs = dict(span.attributes or {})
    assert attrs["outcome"] == "denied"
    assert attrs["denial.reason"] == "invalid_arguments"


def test_a_call_without_context_has_a_denied_span_and_no_org(db: FakeDatabase, telemetry: CapturedTelemetry) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.call_tool("count", {"collection": "new_hires"})

    call(db, fn, header="")
    (span,) = _tool_spans(telemetry)
    attrs = dict(span.attributes or {})
    assert attrs["outcome"] == "denied"
    assert attrs["denial.reason"] == "no_call_context"
    assert "org_id" not in attrs


def test_a_collection_name_off_the_pattern_is_not_put_on_the_span(
    db: FakeDatabase, telemetry: CapturedTelemetry
) -> None:
    async def fn(session: ClientSession) -> Any:
        return await session.call_tool("count", {"collection": CONTENT + ".x"})

    call(db, fn)
    (span,) = _tool_spans(telemetry)
    assert CONTENT not in _all_text(span)


def test_an_unexpected_failure_has_an_error_span_without_its_message(
    db: FakeDatabase, telemetry: CapturedTelemetry, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError(CONTENT)

    monkeypatch.setattr(db["new_hires"], "count_documents", boom)

    async def fn(session: ClientSession) -> Any:
        return await session.call_tool("count", {"collection": "new_hires"})

    call(db, fn)
    (span,) = _tool_spans(telemetry)
    attrs = dict(span.attributes or {})
    assert attrs["outcome"] == "error"
    assert attrs["error.type"] == "RuntimeError"
    assert CONTENT not in _all_text(span)
