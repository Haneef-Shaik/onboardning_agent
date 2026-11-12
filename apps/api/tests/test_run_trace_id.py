"""A run shows its trace id, so the web app can link to the trace (UI-5; PRD §7.11, U7).

The id is the trace-id part of the traceparent stored on the run (F-4). Nothing else of the traceparent
(the parent span, the flags) and nothing of a malformed one reaches the API.
"""

from __future__ import annotations

from typing import Any

import pytest
from onb_api.runs.records import run_record, run_summary

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_MISSING = object()


def _run(traceparent: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "_id": "run_1",
        "hire_id": "h_1",
        "template_key": "engineer-india",
        "template_version": 1,
        "dag_hash": "abc",
        "status": "running",
        "created_by": "u_acme_hr1",
        "created_at": "2026-11-12T09:00:00Z",
        "updated_at": "2026-11-12T09:00:00Z",
    }
    if traceparent is not _MISSING:
        run["traceparent"] = traceparent
    return run


def test_the_trace_id_is_the_trace_part_of_the_stored_traceparent() -> None:
    record = run_record(_run(f"00-{TRACE}-00f067aa0ba902b7-01"), [])
    assert record.trace_id == TRACE


@pytest.mark.parametrize(
    "stored",
    [None, _MISSING, "", "garbage", f"00-{TRACE.upper()}-00f067aa0ba902b7-01", f"00-{'0' * 32}-00f067aa0ba902b7-01", 7],
)
def test_a_missing_or_malformed_traceparent_gives_no_trace_id(stored: Any) -> None:
    assert run_record(_run(stored), []).trace_id is None


def test_the_summary_has_no_trace_id() -> None:
    assert "trace_id" not in run_summary(_run(f"00-{TRACE}-00f067aa0ba902b7-01")).model_dump()
