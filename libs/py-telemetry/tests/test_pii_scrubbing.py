"""OBS-1: raw PII never leaves the process in a span or a log record; `ptk_` tokens do (PRD §7.7, §7.10).

The values below are synthetic: shaped like an Indian mobile, a PAN and an email, belonging to nobody.
Every check reads what was actually exported (or emitted) and looks for any fragment of a raw value.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from onb_telemetry import setup
from onb_telemetry.log_scrub import PiiScrubbingLogFilter, scrub_log_record, withheld_log_record
from onb_telemetry.pipeline import RedactingSpanProcessor, copy_span
from onb_telemetry.settings import TelemetrySettings
from onb_telemetry.span_scrub import PiiScrubbingSpanProcessor
from onb_telemetry.testing import CapturedTelemetry, captured_telemetry, reset_telemetry_globals
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, Status, StatusCode
from opentelemetry.util.types import AttributeValue

MOBILE = "+91 98765 43210"
MOBILE_INT = 9876543210
PAN = "ABCDE1234F"
EMAIL = "jane.doe@example.com"
PTK_REF = "ptk_01JD8X3Q7M9V2K4N6P8R0T2W4Y"
RAW_FRAGMENTS = ("98765", "43210", "9876543210", PAN, EMAIL, "jane.doe", "example.com")


def assert_no_raw_value(text: str) -> None:
    leaked = [fragment for fragment in RAW_FRAGMENTS if fragment in text]
    assert not leaked, f"raw PII fragments leaked: {leaked}"


def exported_json(span: ReadableSpan) -> str:
    return str(span.to_json(indent=None))


# --- Box 1: a span with a mobile, PAN and email in attributes and events exports with markers only


def test_span_with_pii_in_attributes_and_events_exports_with_markers_only(telemetry: CapturedTelemetry) -> None:
    tracer = trace.get_tracer("obs-1")
    with tracer.start_as_current_span(
        "hire.create",
        attributes={"hire.mobile": MOBILE, "hire.pan": PAN, "hire.email": EMAIL, "note": f"call {MOBILE} or {EMAIL}"},
    ) as span:
        span.add_event("contact.captured", {"mobile": MOBILE, "pan": PAN, "email": EMAIL})
        span.add_event(f"sent to {EMAIL}")

    (exported,) = telemetry.spans()
    attrs = dict(exported.attributes or {})
    assert attrs == {
        "hire.mobile": "[MOBILE]",
        "hire.pan": "[PAN]",
        "hire.email": "[EMAIL]",
        "note": "call [MOBILE] or [EMAIL]",
    }
    first, second = exported.events
    assert dict(first.attributes or {}) == {"mobile": "[MOBILE]", "pan": "[PAN]", "email": "[EMAIL]"}
    assert second.name == "sent to [EMAIL]"
    assert_no_raw_value(exported_json(exported))


def test_pii_in_every_other_span_field_is_scrubbed(telemetry: CapturedTelemetry) -> None:
    tracer = trace.get_tracer("obs-1")
    with tracer.start_as_current_span("seed") as seed:
        link = Link(seed.get_span_context(), {"linked.email": EMAIL})
    with tracer.start_as_current_span(f"GET /hires/{PAN}", links=[link]) as span:
        span.set_attribute("hire.mobile_number", MOBILE_INT)
        span.set_attribute("hire.contacts", [EMAIL, PTK_REF, "plain"])
        span.set_attribute("hire.numbers", [MOBILE_INT, 7])
        span.set_attribute(f"contact {EMAIL}", True)
        span.set_status(Status(StatusCode.ERROR, f"no hire with {MOBILE}"))
        span.record_exception(ValueError(f"duplicate PAN {PAN} for {EMAIL}"))

    exported = telemetry.spans()[-1]
    attrs = dict(exported.attributes or {})
    assert exported.name == "GET /hires/[PAN]"
    assert attrs["hire.mobile_number"] == "[MOBILE]"
    assert attrs["hire.contacts"] == ("[EMAIL]", PTK_REF, "plain")
    assert attrs["hire.numbers"] == ("[MOBILE]", "7")
    assert attrs["contact [EMAIL]"] is True
    assert exported.status.description == "no hire with [MOBILE]"
    (event,) = exported.events
    assert dict(event.attributes or {})["exception.message"] == "duplicate PAN [PAN] for [EMAIL]"
    assert dict(exported.links[0].attributes or {}) == {"linked.email": "[EMAIL]"}
    assert_no_raw_value(exported_json(exported))


def test_scrubbing_runs_after_every_hook_so_a_hook_cannot_reintroduce_pii(telemetry: CapturedTelemetry) -> None:
    class Enricher(RedactingSpanProcessor):
        def redact(self, span: ReadableSpan) -> ReadableSpan | None:
            return copy_span(span, attributes={**(span.attributes or {}), "added.email": EMAIL})

    telemetry.handle.register_span_processor(Enricher())
    with trace.get_tracer("obs-1").start_as_current_span("enriched"):
        pass

    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == {"added.email": "[EMAIL]"}


def test_a_span_without_pii_is_exported_as_is(telemetry: CapturedTelemetry) -> None:
    attributes: dict[str, AttributeValue] = {
        "org_id": "org_acme",
        "http.status_code": 200,
        "ratio": 0.5,
        "ok": True,
        "ids": ("a", "b"),
    }
    with trace.get_tracer("obs-1").start_as_current_span("clean", attributes=attributes):
        pass

    (exported,) = telemetry.spans()
    assert exported.name == "clean"
    assert dict(exported.attributes or {}) == attributes


def test_a_failing_scrubber_drops_the_span_rather_than_export_it_raw(
    telemetry: CapturedTelemetry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: str) -> str:
        raise RuntimeError("detector broke")

    monkeypatch.setattr("onb_telemetry.span_scrub.redact", boom)
    with trace.get_tracer("obs-1").start_as_current_span("x", attributes={"pan": PAN}):
        pass

    assert telemetry.spans() == ()


# --- Box 2: a `ptk_` token passes through unchanged


def test_a_pii_token_passes_through_a_span_unchanged(telemetry: CapturedTelemetry) -> None:
    tracer = trace.get_tracer("obs-1")
    with tracer.start_as_current_span(f"notify {PTK_REF}", attributes={"recipient": PTK_REF}) as span:
        span.add_event("message.queued", {"recipient": PTK_REF, "text": f"Hi, your manager is {PTK_REF}"})

    (exported,) = telemetry.spans()
    assert exported.name == f"notify {PTK_REF}"
    assert dict(exported.attributes or {}) == {"recipient": PTK_REF}
    assert dict(exported.events[0].attributes or {}) == {"recipient": PTK_REF, "text": f"Hi, your manager is {PTK_REF}"}


def test_a_pii_token_passes_through_a_log_record_unchanged() -> None:
    record = _record("sending to %s", PTK_REF, extra={"recipient": PTK_REF})

    scrubbed = scrub_log_record(record)

    assert scrubbed.getMessage() == f"sending to {PTK_REF}"
    assert scrubbed.__dict__["recipient"] == PTK_REF


def test_a_token_next_to_a_raw_value_keeps_the_token_and_loses_the_value(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("obs-1").start_as_current_span("t", attributes={"pair": f"{PTK_REF} {PAN}"}):
        pass

    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == {"pair": f"{PTK_REF} [PAN]"}


# --- Box 3: a log record carrying the same values is scrubbed


def _record(msg: str, *args: object, extra: dict[str, object] | None = None, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise ValueError(f"duplicate {PAN} for {EMAIL}")
        except ValueError as error:
            exc_info = (type(error), error, error.__traceback__)
    record = logging.LogRecord("svc", logging.ERROR, __file__, 1, msg, args, exc_info)
    record.__dict__.update(extra or {})
    return record


def _format(record: logging.LogRecord) -> str:
    return logging.Formatter("%(levelname)s %(name)s %(message)s").format(record)


def test_a_log_record_carrying_the_same_values_is_scrubbed() -> None:
    record = _record(f"hire {EMAIL} mobile %s pan %s", MOBILE, PAN, extra={"hire_email": EMAIL}, exc=True)
    record.stack_info = f"Stack (most recent call last):\n  lookup({MOBILE_INT})"

    scrubbed = scrub_log_record(record)

    assert scrubbed.getMessage() == "hire [EMAIL] mobile [MOBILE] pan [PAN]"
    assert scrubbed.args == ("[MOBILE]", "[PAN]")  # kept apart, scrubbed, for formatters that read args
    assert scrubbed.__dict__["hire_email"] == "[EMAIL]"
    assert scrubbed.exc_info is None
    assert "ValueError: duplicate [PAN] for [EMAIL]" in (scrubbed.exc_text or "")
    assert "lookup([MOBILE])" in (scrubbed.stack_info or "")
    line = _format(scrubbed)
    assert_no_raw_value(line)
    assert "ValueError: duplicate [PAN] for [EMAIL]" in line


def test_scrubbing_a_log_record_does_not_change_the_original() -> None:
    record = _record("pan %s", PAN, extra={"hire_email": EMAIL})

    scrub_log_record(record)

    assert record.getMessage() == f"pan {PAN}"
    assert record.__dict__["hire_email"] == EMAIL


def test_a_log_record_whose_arguments_do_not_format_is_still_scrubbed() -> None:
    record = _record("pan %s and %s", PAN)  # one argument short

    scrubbed = scrub_log_record(record)

    assert_no_raw_value(scrubbed.getMessage())
    assert "[PAN]" in scrubbed.getMessage()


def test_the_log_filter_hands_on_a_scrubbed_copy() -> None:
    lines: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(_format(record) + f" email={record.__dict__.get('hire_email')}")

    handler = ListHandler()
    handler.addFilter(PiiScrubbingLogFilter())
    logger = logging.getLogger("obs-1.filter")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("mobile %s pan %s", MOBILE, PAN, extra={"hire_email": EMAIL})
    finally:
        logger.removeHandler(handler)
        logger.propagate = True

    (line,) = lines
    assert line == "WARNING obs-1.filter mobile [MOBILE] pan [PAN] email=[EMAIL]"


# --- Box 4: `init_telemetry` installs the processor (and the log scrubbing) by default


@pytest.fixture
def plain_init() -> Iterator[tuple[setup.TelemetryHandle, InMemorySpanExporter]]:
    """`init_telemetry` called as a service calls it: no processors, no test helper wiring."""
    reset_telemetry_globals()
    exporter = InMemorySpanExporter()
    handle = setup.init_telemetry("svc", exporter=exporter, settings=TelemetrySettings(exporter="none"), batch=False)
    try:
        yield handle, exporter
    finally:
        reset_telemetry_globals()


def test_init_telemetry_installs_the_pii_span_processor_by_default(
    plain_init: tuple[setup.TelemetryHandle, InMemorySpanExporter],
) -> None:
    handle, exporter = plain_init
    assert isinstance(handle.pipeline.final_hook, PiiScrubbingSpanProcessor)

    with trace.get_tracer("obs-1").start_as_current_span("x", attributes={"pan": PAN, "email": EMAIL}):
        pass

    (exported,) = exporter.get_finished_spans()
    assert dict(exported.attributes or {}) == {"pan": "[PAN]", "email": "[EMAIL]"}


def test_init_telemetry_installs_log_scrubbing_by_default(
    plain_init: tuple[setup.TelemetryHandle, InMemorySpanExporter], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="obs-1.app"):
        logging.getLogger("obs-1.app").info("hire %s with mobile %s", EMAIL, MOBILE)

    (record,) = [r for r in caplog.records if r.name == "obs-1.app"]
    assert record.getMessage() == "hire [EMAIL] with mobile [MOBILE]"
    assert_no_raw_value(caplog.text)


def _make(message: str, *args: object) -> logging.LogRecord:
    return logging.getLogger("obs-1.make").makeRecord("x", logging.INFO, __file__, 1, message, args, None)


def test_log_scrubbing_outlives_shutdown_and_only_the_test_reset_removes_it() -> None:
    reset_telemetry_globals()
    original_make_record = logging.Logger.makeRecord
    root_handler = logging.NullHandler()
    logging.getLogger().addHandler(root_handler)
    try:
        handle = setup.init_telemetry("svc", settings=TelemetrySettings(exporter="none"), instrument=False)
        assert any(isinstance(f, PiiScrubbingLogFilter) for f in root_handler.filters)
        handle.shutdown()  # lifespan teardown and atexit handlers still log after this
        assert any(isinstance(f, PiiScrubbingLogFilter) for f in root_handler.filters)
        assert _make("pan %s", PAN).getMessage() == "pan [PAN]"

        reset_telemetry_globals()
        assert not any(isinstance(f, PiiScrubbingLogFilter) for f in root_handler.filters)
        assert logging.Logger.makeRecord is original_make_record
        assert _make("pan %s", PAN).getMessage() == f"pan {PAN}"  # the test process is back as it was
    finally:
        logging.getLogger().removeHandler(root_handler)
        reset_telemetry_globals()


# --- Review findings: non-string extras, account numbers under account-like keys, resource, scope


def test_non_string_extra_fields_are_scrubbed_and_harmless_ones_kept() -> None:
    marker = object()
    record = _record(
        "created",
        extra={
            "user": {"email": EMAIL, "pans": [PAN], "ref": PTK_REF},
            "hire_mobile": MOBILE_INT,
            "raw": MOBILE.encode(),
            "count": 3,
            "flag": None,
            "obj": marker,
            f"hire {EMAIL}": 1,
        },
    )

    fields = scrub_log_record(record).__dict__

    assert fields["user"] == {"email": "[EMAIL]", "pans": ["[PAN]"], "ref": PTK_REF}
    assert fields["hire_mobile"] == "[MOBILE]"
    assert "[MOBILE]" in str(fields["raw"])
    assert (fields["count"], fields["flag"], fields["obj"]) == (3, None, marker)
    assert fields["hire [EMAIL]"] == 1
    assert_no_raw_value(repr(fields))


ACCOUNT_NO = "50100123456789"


@pytest.mark.parametrize("key", ["bank_account", "employee.bank_account", "acct", "account_number", "bank.acc_no"])
def test_a_bare_account_number_under_an_account_key_is_scrubbed(telemetry: CapturedTelemetry, key: str) -> None:
    with trace.get_tracer("obs-1").start_as_current_span(
        "a", attributes={key: ACCOUNT_NO, f"{key}_int": 50100123456789}
    ):
        pass
    record = scrub_log_record(_record("x", extra={key: ACCOUNT_NO}))

    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == {key: "[BANK_ACCOUNT]", f"{key}_int": "[BANK_ACCOUNT]"}
    assert record.__dict__[key] == "[BANK_ACCOUNT]"


def test_numbers_under_other_keys_are_not_mistaken_for_accounts(telemetry: CapturedTelemetry) -> None:
    attributes: dict[str, AttributeValue] = {"order_id": ACCOUNT_NO, "http.response.size": 50100123456789}
    with trace.get_tracer("obs-1").start_as_current_span("a", attributes=attributes):
        pass

    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == attributes


def test_resource_and_scope_attributes_are_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"deployment.owner={EMAIL}")
    with captured_telemetry() as captured:
        tracer = trace.get_tracer("obs-1", attributes={"scope.owner": EMAIL})
        with tracer.start_as_current_span("r"):
            pass
        (exported,) = captured.spans()

    assert exported.resource.attributes["deployment.owner"] == "[EMAIL]"
    assert exported.instrumentation_scope is not None
    assert dict(exported.instrumentation_scope.attributes or {}) == {"scope.owner": "[EMAIL]"}
    assert_no_raw_value(exported_json(exported))


def test_log_scrubbing_is_in_place_for_records_made_through_the_factory(
    plain_init: tuple[setup.TelemetryHandle, InMemorySpanExporter],
) -> None:
    record = logging.getLogger("obs-1.factory").makeRecord(
        "obs-1.factory", logging.INFO, __file__, 1, "pan %s", (PAN,), None
    )
    assert record.getMessage() == "pan [PAN]"


def test_extra_fields_are_scrubbed_for_a_handler_added_after_init(
    plain_init: tuple[setup.TelemetryHandle, InMemorySpanExporter],
) -> None:
    seen: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logger = logging.getLogger("obs-1.late")
    logger.propagate = False
    logger.addHandler(Keep())
    try:
        logger.warning("hire created", extra={"hire_email": EMAIL, "hire_mobile": MOBILE, "recipient": PTK_REF})
    finally:
        logger.handlers.clear()
        logger.propagate = True

    (record,) = seen
    assert (record.__dict__["hire_email"], record.__dict__["hire_mobile"]) == ("[EMAIL]", "[MOBILE]")
    assert record.__dict__["recipient"] == PTK_REF


def test_exported_spans_serialise_to_json_without_raw_values(telemetry: CapturedTelemetry) -> None:
    with trace.get_tracer("obs-1").start_as_current_span("j", attributes={"blob": json.dumps({"pan": PAN})}):
        pass

    (exported,) = telemetry.spans()
    assert json.loads(str(dict(exported.attributes or {})["blob"])) == {"pan": "[PAN]"}


# --- Log scrubbing fails closed without breaking the caller


def _break_detectors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: str) -> str:
        raise RuntimeError("detector broke")

    monkeypatch.setattr("onb_telemetry.log_scrub.redact", boom)


def test_a_failing_log_scrubber_withholds_the_record_without_breaking_the_caller(
    plain_init: tuple[setup.TelemetryHandle, InMemorySpanExporter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logger = logging.getLogger("obs-1.broken")
    logger.propagate = False  # only this handler, which has no filter: the makeRecord path alone
    logger.addHandler(Keep())
    _break_detectors(monkeypatch)
    try:
        try:
            raise ValueError(f"bad {PAN}")
        except ValueError:
            logger.exception("pan %s", PAN, extra={"hire_email": EMAIL}, stack_info=True)  # must not raise
    finally:
        logger.handlers.clear()
        logger.propagate = True

    (record,) = seen
    assert record.getMessage() == "[log record withheld: PII scrubbing failed with RuntimeError]"
    assert (record.exc_info, record.exc_text, record.stack_info) == (None, None, None)
    assert record.__dict__["hire_email"] == "[withheld]"
    assert_no_raw_value(_format(record) + repr(record.__dict__))


def test_a_failing_log_filter_drops_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _break_detectors(monkeypatch)

    assert PiiScrubbingLogFilter().filter(_record("pan %s", PAN)) is False


def test_a_formatter_that_unpacks_args_keeps_working() -> None:
    """uvicorn's AccessFormatter unpacks `record.args` into five values; scrubbing must keep them."""

    class AccessLike(logging.Formatter):
        def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802 - stdlib override
            client, method, path, version, status = record.__dict__["args"]
            return f"{client} {method} {path} HTTP/{version} {status}"

    record = _record('%s - "%s %s HTTP/%s" %d', "10.0.0.7:5100", "GET", f"/hires/{EMAIL}", "1.1", 200)

    line = AccessLike().format(scrub_log_record(record))

    assert line == "10.0.0.7:5100 GET /hires/[EMAIL] HTTP/1.1 200"


def test_a_value_split_between_template_and_argument_is_still_scrubbed() -> None:
    scrubbed = scrub_log_record(_record("mobile 98765 %s", "43210"))

    assert scrubbed.getMessage() == "mobile [MOBILE]"


@pytest.mark.parametrize(
    ("key", "hex_id"),
    [
        ("otelTraceID", "0af7651916cd43dd84eb2a9876543210"),
        ("trace_id", "0af7651916cd43dd84eb2a9876543210"),
        ("otelSpanID", "ab9876543210cdef"),
        ("span_id", "ab9876543210cdef"),
    ],
)
def test_trace_correlation_ids_are_not_mangled(key: str, hex_id: str) -> None:
    assert scrub_log_record(_record("x", extra={key: hex_id})).__dict__[key] == hex_id


def test_a_mobile_under_a_trace_id_key_is_still_scrubbed() -> None:
    assert scrub_log_record(_record("x", extra={"trace_id": MOBILE})).__dict__["trace_id"] == "[MOBILE]"


def test_a_ten_digit_number_starting_6_to_9_is_treated_as_a_mobile_by_design(telemetry: CapturedTelemetry) -> None:
    """The documented trade-off: a number shaped like a valid mobile is scrubbed whatever it means."""
    with trace.get_tracer("obs-1").start_as_current_span("n", attributes={"db.rows": 7000000000, "small": 70}):
        pass

    (exported,) = telemetry.spans()
    assert dict(exported.attributes or {}) == {"db.rows": "[MOBILE]", "small": 70}


def test_dropped_attribute_counts_survive_scrubbing() -> None:
    with captured_telemetry() as captured:
        captured.handle.provider._span_limits = SpanLimits(max_span_attributes=1)
        with trace.get_tracer("obs-1").start_as_current_span(
            "d", attributes={"a": "x", "b": PAN}
        ):  # the SDK drops the oldest
            pass
        (exported,) = captured.spans()

    assert dict(exported.attributes or {}) == {"b": "[PAN]"}
    assert exported.dropped_attributes == 1


def test_a_withheld_record_keeps_custom_fields_as_placeholders() -> None:
    withheld = withheld_log_record(_record("x", extra={"otelTraceID": "abc", "hire_email": EMAIL}), RuntimeError())

    line = logging.Formatter("%(otelTraceID)s %(message)s").format(withheld)

    assert line == "[withheld] [log record withheld: PII scrubbing failed with RuntimeError]"
    assert withheld.__dict__["hire_email"] == "[withheld]"
