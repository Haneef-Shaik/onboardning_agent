"""A consumer's span joins the producer's trace (EVT-1 box 4; PRD §5.7, §7.2 `traceparent`).

`publish` opens a PRODUCER span and stores its `traceparent` on the event and every job. The worker
runs the handler inside a CONSUMER span continued from the job's `traceparent`, so a run stays one
trace across the hand-off. The same test against a real MongoDB is in `tests_int/`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from onb_data import tenant_scope
from onb_eventbus import JobContext
from onb_telemetry.testing import CapturedTelemetry
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from .helpers import CONSUMER, EVENT, ORG_A, make_bus


def _by_name(captured: CapturedTelemetry) -> dict[str, Any]:
    return {span.name: span for span in captured.spans()}


def test_a_consumer_span_joins_the_producer_trace(telemetry: CapturedTelemetry) -> None:
    bus, db, _ = make_bus()
    seen_in_handler: list[int] = []

    async def handler(job: JobContext) -> None:
        seen_in_handler.append(trace.get_current_span().get_span_context().trace_id)

    async def produce() -> None:
        tracer = trace.get_tracer("test.api")
        with tracer.start_as_current_span("POST /runs"), tenant_scope(ORG_A):
            await bus.publish(EVENT, {"run_id": "r1"}, idempotency_key="k1", run_id="r1")

    async def consume() -> None:
        # A separate task with no current span, as in a worker process.
        assert not trace.get_current_span().get_span_context().is_valid
        await bus.worker({CONSUMER: handler}).run_once()

    asyncio.run(produce())
    asyncio.run(consume())

    spans = _by_name(telemetry)
    request, publish, consume_span = spans["POST /runs"], spans[f"publish {EVENT}"], spans[f"consume {CONSUMER}"]
    trace_id = request.context.trace_id
    assert publish.context.trace_id == trace_id
    assert consume_span.context.trace_id == trace_id
    assert consume_span.parent.span_id == publish.context.span_id
    assert seen_in_handler == [trace_id]
    assert publish.kind is SpanKind.PRODUCER
    assert consume_span.kind is SpanKind.CONSUMER
    assert db["events"].docs[0]["traceparent"] == db["jobs"].docs[0]["traceparent"]
    assert format(publish.context.span_id, "016x") in db["jobs"].docs[0]["traceparent"]


def test_spans_carry_org_and_run_and_never_the_payload(telemetry: CapturedTelemetry) -> None:
    bus, _, _ = make_bus()

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(EVENT, {"email": "secret@example.com"}, idempotency_key="k1", run_id="r1")
        await bus.worker({CONSUMER: _noop}).run_once()

    asyncio.run(main())
    spans = _by_name(telemetry)
    for name in (f"publish {EVENT}", f"consume {CONSUMER}"):
        attributes = dict(spans[name].attributes or {})
        assert attributes["org_id"] == ORG_A
        assert attributes["run_id"] == "r1"
        assert "secret@example.com" not in str(attributes)
    assert dict(spans[f"consume {CONSUMER}"].attributes or {})["messaging.message.id"]


def test_a_failed_handler_marks_its_span_as_an_error(telemetry: CapturedTelemetry) -> None:
    bus, _, _ = make_bus()

    async def fail(job: JobContext) -> None:
        raise ValueError("no")

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(EVENT, {}, idempotency_key="k1")
        await bus.worker({CONSUMER: fail}).run_once()

    asyncio.run(main())
    span = _by_name(telemetry)[f"consume {CONSUMER}"]
    assert span.status.status_code is StatusCode.ERROR
    assert "run_id" not in dict(span.attributes or {})


def test_a_failed_publish_traces_the_error_type_but_never_its_message(telemetry: CapturedTelemetry) -> None:
    """Security review: a driver error message can quote a document's values (e.g. a duplicate email)."""
    bus, _, _ = make_bus()

    async def change() -> None:
        raise ValueError("E11000 duplicate key { email: 'secret@example.com' }")

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(EVENT, {}, idempotency_key="k1", change=change)

    with pytest.raises(ValueError, match="E11000"):
        asyncio.run(main())
    span = _by_name(telemetry)[f"publish {EVENT}"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError"
    assert "secret@example.com" not in span.to_json()


def test_a_job_without_a_stored_trace_starts_a_new_one(telemetry: CapturedTelemetry) -> None:
    bus, db, _ = make_bus()

    async def main() -> None:
        with tenant_scope(ORG_A):
            await bus.publish(EVENT, {}, idempotency_key="k1")
        db["jobs"].docs[0].pop("traceparent")
        await bus.worker({CONSUMER: _noop}).run_once()

    asyncio.run(main())
    span = _by_name(telemetry)[f"consume {CONSUMER}"]
    assert span.parent is None


async def _noop(job: JobContext) -> None:
    return None
