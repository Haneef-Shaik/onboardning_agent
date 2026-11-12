"""SEC-1 box 1: a full run with fixture PII leaves no raw value anywhere outside the vault (PRD §12 PII,
§13 "full trace in Jaeger with no PII").

The run (`scenario.walk`) feeds every sensitive field in through the API, resolves the mobile at the
Telegram sink, and puts raw values into free text (hire notes, a chat question). Then everything the card
names is scanned for each raw value and every detector hit: all MongoDB collections but the vault,
`mock_outbox`, the events, the application log, every container's log, the session's traces in Jaeger
and every input any model was sent. The report (`reports/test-pii/pii-scan-report.{json,md}`, or
`$PII_SCAN_REPORT_DIR`) names locations only, never values.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from itertools import chain
from typing import Any, Final

from pymongo import MongoClient

from . import collect
from .conftest import Session
from .fixture import PiiFixture
from .platform import ORG, ROOT
from .report import write_scan_report
from .scan import Allow, ScanResult, scan
from .scenario import Journey, walk
from .stack import Platform

Unscoped = Callable[[str], AbstractContextManager[None]]

# Detector hits excused by design, each for one stated reason. Raw fixture values are never excused.
ALLOW: Final = (
    Allow("mongo", r"users\[\d+\]\.email", "EMAIL", "a platform user's login id (PRD §3 users), not a hire's PII"),
    Allow("container_logs", r"caddy:\d+\.GOMEMLIMIT", "MOBILE", "Caddy's startup line: the Go memory limit in bytes"),
)
MIN_SPANS: Final = 50
SOURCES: Final = (
    "mongo", "mock_outbox", "events", "app_logs", "container_logs", "jaeger", "llm_inputs", "api_responses",
)  # fmt: skip
EXPECTED_TASKS: Final = {
    "collect_docs", "manager_approval", "laptop", "bitbucket_access", "security_training", "orientation",
    "welcome_message",
}  # fmt: skip


def _evidence_of_flow(db: Any, journey: Journey) -> dict[str, Any]:
    """What shows the values really went through the platform, so a clean scan means something."""
    return {
        "run_status": journey.run_status,
        "tasks_done": sorted(t for t, s in journey.tasks.items() if s == "done"),
        "vault_records": db["pii_vault"].count_documents({"org_id": ORG}),
        "aadhaar_vault_records": db["pii_vault_aadhaar"].count_documents({"org_id": ORG}),
        "mock_outbox_messages": db["mock_outbox"].count_documents({"org_id": ORG}),
        "events": db["events"].count_documents({"org_id": ORG}),
        "sink_resolutions": db["pii_access_log"].count_documents({"org_id": ORG}),
        "documents": db["documents_meta"].count_documents({"org_id": ORG}),
        "notes_redacted": "[MOBILE]" in str((db["new_hires"].find_one({"org_id": ORG}) or {}).get("notes")),
        "chat_redacted": db["messages"].count_documents({"org_id": ORG, "content": {"$regex": r"\[(MOBILE|PAN)\]"}}),
    }


def test_a_full_run_leaves_no_raw_pii_outside_the_vault(
    session: Session,
    platform: Platform,
    fixture_pii: PiiFixture,
    root_client: MongoClient[dict[str, Any]],
    unscoped: Unscoped,
) -> None:
    journey = walk(platform.api_url, platform.secrets.password, fixture_pii)
    platform.workers.stop()  # every job done: nothing writes while the scan reads
    assert platform.workers.failures == []
    assert session.telemetry.force_flush(30_000)
    db = root_client[session.database]
    traces = collect.wait_for_jaeger(session.jaeger, session.service, session.started_us, MIN_SPANS)
    with unscoped("SEC-1 scans every collection of the scratch database as root"):
        flow = _evidence_of_flow(db, journey)
        items = list(
            chain(
                collect.mongo_items(db),
                collect.log_file_items(session.log_file),
                collect.container_log_items(ROOT, since_us=session.started_us),
                collect.jaeger_items(traces),
                collect.llm_items(session.llm.calls()),
                collect.response_items(journey.responses),
            )
        )
    result: ScanResult = scan(items, fixture_pii.values(), ALLOW)
    flow["jaeger_spans"] = collect.span_count(traces)
    flow["llm_calls"] = len(session.llm.calls())
    flow["jaeger_traces"] = len({t for t in collect.trace_ids(traces)})
    flow["assistant_saw_the_redacted_question"] = any(
        "Did the welcome reach [MOBILE]" in json.dumps(messages) for _, messages in session.llm.calls()
    )
    flow["invalid_input_refused_with"] = journey.rejected_input
    flow["api_responses"] = len(journey.responses)
    write_scan_report(session.report_dir, result, flow, journey)
    print(f"\nSEC-1 PII scan: {json.dumps(result.report()['items_scanned_by_source'])}, hits={len(result.hits)}")

    # Every unmet check and every hit, reported together (a leak often breaks a flow check too).
    checks = {
        "the run completed": flow["run_status"] == "completed",
        "every non-approval task is done": set(flow["tasks_done"]) >= EXPECTED_TASKS - {"manager_approval"},
        "six fields and the superseded mobile are in the vault": flow["vault_records"] >= 7,
        "the Aadhaar is in its own partition": flow["aadhaar_vault_records"] == 1,
        "the welcome message reached mock_outbox": flow["mock_outbox_messages"] >= 1,
        "the sink resolved the mobile with a grant": flow["sink_resolutions"] >= 1,
        "the scan was stored": flow["documents"] == 1,
        "the hire notes are stored redacted": flow["notes_redacted"],
        "the chat question is stored redacted": flow["chat_redacted"] >= 1,
        f"Jaeger holds at least {MIN_SPANS} spans of the session": flow["jaeger_spans"] >= MIN_SPANS,
        "every agent task called its model": flow["llm_calls"] >= len(EXPECTED_TASKS),
        "the run assistant's model got the redacted question": flow["assistant_saw_the_redacted_question"],
        "the invalid hire was refused with 422": flow["invalid_input_refused_with"] == 422,
        f"Jaeger answered below its page size ({collect.JAEGER_PAGE} traces)": flow["jaeger_traces"]
        < collect.JAEGER_PAGE,
        **{f"something was scanned from {s}": result.items_by_source.get(s, 0) > 0 for s in SOURCES},
    }
    problems = [f"check failed: {name}" for name, ok in checks.items() if not ok]
    problems += [hit.describe() for hit in result.hits]
    assert problems == [], "\n".join(problems)
