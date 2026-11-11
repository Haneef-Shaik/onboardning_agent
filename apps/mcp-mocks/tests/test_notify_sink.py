"""The notification sink (NOTIF-1 boxes 1 and 2; PRD §7.10 sink flow, FR-N1).

Box 1: `mock_outbox` holds only the masked mobile, never the raw value (DB scan test).
Box 2: a rejected grant stores nothing, fails the task, and writes a guardrail event and an audit record.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from onb_crypto import GrantIssuer, VaultField
from onb_data import tenant_scope
from onb_mcp_mocks.notify.audit import Caller
from onb_mcp_mocks.notify.sink import SinkRefusedError
from onb_telemetry.testing import CapturedTelemetry

from .notify_world import (
    B_MOBILE,
    CALLER,
    HIRE,
    MOBILE,
    ORG_A,
    ORG_B,
    OTHER_HIRE,
    OTHER_MOBILE,
    POLICY,
    MovableClock,
    World,
    build_world,
)

RAW_FORMS = (MOBILE, MOBILE[2:-2], int(MOBILE), OTHER_MOBILE, OTHER_MOBILE[2:-2], B_MOBILE, B_MOBILE[2:-2])
NOT_SCANNED = frozenset({"pii_vault", "org_keys"})  # ciphertext and wrapped keys only


@pytest.fixture
def world() -> World:
    return build_world()


def raw_hits(world: World) -> list[str]:
    """Every stored document (any org, any collection outside the vault) that holds a raw value."""
    hits: list[str] = []
    for collection, document in world.db.all_documents():
        if collection in NOT_SCANNED:
            continue
        text = json.dumps(document, default=str)
        hits.extend(f"{collection}: {form}" for form in RAW_FORMS if str(form) in text)
    return hits


# --- box 1 --------------------------------------------------------------------------------------------


def test_box1_mock_outbox_holds_only_the_masked_mobile_never_the_raw_value(world: World) -> None:
    result = world.send(world.grant(), world.telegram())
    assert result.status == "sent"
    assert not result.replayed
    outbox = world.db.raw("mock_outbox")
    assert len(outbox) == 1
    stored = outbox[0]
    assert stored["org_id"] == ORG_A
    assert stored["recipient"] == {"token": world.tokens["mobile"], "masked": "98XXXXXX34"}
    assert stored["text"] == "Hi Priya, welcome aboard on 1 December 2026!"
    assert (stored["run_id"], stored["task_id"], stored["agent"]) == (
        "run_acme_001",
        "welcome_message",
        "templates_agent",
    )
    # The DB scan: no collection outside the vault holds the raw mobile or its hidden digits.
    assert raw_hits(world) == []
    # The value did reach the transport ("sent"), and exactly one masked access log entry exists.
    assert world.transport.sent == 1
    access = world.db.raw("pii_access_log")
    assert [(a["field"], a["masked"], a["subject"]["id"]) for a in access] == [("mobile", "98XXXXXX34", HIRE)]


def test_a_send_publishes_notification_sent_with_the_outbox_write(world: World) -> None:
    world.send(world.grant(), world.telegram())
    [(event, payload)] = world.events.published
    outbox_id = world.db.raw("mock_outbox")[0]["_id"]
    assert event == "notification.sent"
    assert {k: payload[k] for k in ("outbox_id", "channel", "run_id", "task_id")} == {
        "outbox_id": outbox_id,
        "channel": "telegram",
        "run_id": "run_acme_001",
        "task_id": "welcome_message",
    }


def test_the_same_grant_and_arguments_again_is_a_replay_that_sends_nothing_new(world: World) -> None:
    grant = world.grant()
    first = world.send(grant, world.telegram())
    again = world.send(grant, world.telegram())
    assert again.replayed
    assert again.notification_id == first.notification_id
    assert world.transport.sent == 1
    assert len(world.db.raw("mock_outbox")) == 1
    assert len(world.db.raw("pii_access_log")) == 1


def test_the_same_grant_with_other_arguments_is_a_rejected_replay(world: World) -> None:
    grant = world.grant()
    world.send(grant, world.telegram())
    with pytest.raises(SinkRefusedError) as refused:
        world.send(grant, world.telegram(body="A different message"))
    assert refused.value.reason == "grant_rejected:replayed"
    assert len(world.db.raw("mock_outbox")) == 1


# --- box 2 ------------------------------------------------------------------------------------------


def rejection_cases(world: World) -> dict[str, tuple[str | None, dict[str, Any], str]]:
    """name -> (grant, arguments, expected reason)."""
    forged = GrantIssuer(world.other_key, POLICY)
    with tenant_scope(ORG_A):
        forged_grant = forged.issue_grant(
            ORG_A,
            run_id="run_acme_001",
            task_id="welcome_message",
            subject_id=HIRE,
            fields=[VaultField.MOBILE],
            sink="notify.telegram",
        )
    return {
        "no-grant": (None, world.telegram(), "malformed"),
        "forged-signature": (forged_grant, world.telegram(), "bad_signature"),
        "wrong-sink": (world.grant(fields=[VaultField.EMAIL], sink="notify.email"), world.telegram(), "wrong_sink"),
        "another-hires-token": (world.grant(), world.telegram("other"), "token_not_bound"),
        "another-orgs-token": (world.grant(), world.telegram("b_mobile"), "token_not_found"),
        "another-orgs-grant": (world.grant(org=ORG_B, subject="h_globex_001"), world.telegram("b_mobile"), "wrong_org"),
        "email-token-as-recipient": (world.grant(), world.telegram("email"), "token_not_bound"),
    }


CASES = [
    "no-grant",
    "forged-signature",
    "wrong-sink",
    "another-hires-token",
    "another-orgs-token",
    "another-orgs-grant",
    "email-token-as-recipient",
]


@pytest.mark.parametrize("case", CASES)
def test_box2_a_rejected_grant_stores_nothing_fails_the_task_and_writes_a_guardrail_event_and_an_audit_record(
    world: World, telemetry: CapturedTelemetry, case: str
) -> None:
    grant, args, reason = rejection_cases(world)[case]
    before = {name: len(world.db.raw(name)) for name in ("mock_outbox", "pii_access_log", "notifications")}
    with pytest.raises(SinkRefusedError) as refused:
        world.send(grant, args)
    # Nothing sent, nothing stored.
    assert refused.value.reason == f"grant_rejected:{reason}"
    assert world.transport.sent == 0
    assert (
        {name: len(world.db.raw(name)) for name in before}
        == before
        == {
            "mock_outbox": 0,
            "pii_access_log": 0,
            "notifications": 0,
        }
    )
    assert raw_hits(world) == []
    # The task fails: the orchestrator is told through the bus.
    [(event, payload)] = world.events.published
    assert event == "task.sink_rejected"
    assert payload["run_id"] == "run_acme_001"
    assert payload["agent"] == "templates_agent"
    assert (payload["sink"], payload["reason"]) == ("notify.telegram", reason)
    # An audit record (the grant) and a guardrail decision (audit record + span event), in org A.
    records = {(r.action, r.target.split(":")[0], r.outcome.value, r.reason) for r in world.records(ORG_A)}
    assert ("pii.grant.resolve", "pii_grant", "denied", reason) in records
    assert ("guardrail.sink.grant", "task", "denied", reason) in records
    assert world.records(ORG_B) == []
    events = [e for s in telemetry.spans() for e in s.events if e.name == "guardrail.outcome"]
    assert [(e.attributes or {}).get("guardrail.check") for e in events] == ["sink.grant"]
    assert (events[0].attributes or {}).get("guardrail.reason") == reason


def test_a_verified_grants_task_is_named_in_the_failure_and_the_guardrail_target(world: World) -> None:
    with pytest.raises(SinkRefusedError):
        world.send(world.grant(task="welcome_message"), world.telegram("other"))
    [(_, payload)] = world.events.published
    assert payload["task_id"] == "welcome_message"
    targets = {r.target for r in world.records() if r.action == "guardrail.sink.grant"}
    assert targets == {"task:run_acme_001/welcome_message"}


def test_a_send_sweeps_this_orgs_expired_jti_rows_and_keeps_live_ones() -> None:
    clock = MovableClock()
    world = build_world(clock=clock)
    world.send(world.grant(ttl=timedelta(seconds=1)), world.telegram())
    org_b = Caller(org_id=ORG_B, agent="templates_agent", run_id="run_acme_001")
    world.send(
        world.grant(org=ORG_B, subject="h_globex_001", ttl=timedelta(seconds=1)), world.telegram("b_mobile"), org_b
    )
    first = {row["org_id"]: row["jti"] for row in world.db.raw("pii_grant_jti")}
    clock.advance(timedelta(seconds=3))  # both first grants have expired; a new one lives 5 minutes
    world.send(world.grant(), world.telegram(body="Second message"))
    rows = world.db.raw("pii_grant_jti")
    assert first[ORG_A] not in {row["jti"] for row in rows}, "org A's expired row was not swept"
    assert first[ORG_B] in {row["jti"] for row in rows}, "an org-A send swept org B's row"
    assert sorted(row["org_id"] for row in rows) == [ORG_A, ORG_B]  # org A's live grant stays


def test_a_body_carrying_a_token_is_refused_before_any_grant_is_used(world: World) -> None:
    grant = world.grant()
    with pytest.raises(SinkRefusedError) as refused:
        world.send(grant, world.telegram(body=f"Call {world.tokens['other']}"))
    assert refused.value.reason == "invalid_arguments"
    assert world.db.raw("pii_grant_jti") == []
    assert world.transport.sent == 0
    assert world.send(grant, world.telegram()).status == "sent"  # the grant is still unused


# --- in-app -------------------------------------------------------------------------------------------


def test_in_app_stores_a_notification_for_a_user_of_the_callers_org(world: World) -> None:
    result = world.send(None, {"channel": "in_app", "recipient_user_id": "u_acme_hr1", "body": "Welcome sent."})
    [stored] = world.db.raw("notifications")
    assert stored["_id"] == result.notification_id
    assert (stored["org_id"], stored["user_id"], stored["kind"], stored["agent"]) == (
        ORG_A,
        "u_acme_hr1",
        "agent_message",
        "templates_agent",
    )
    assert world.db.raw("pii_grant_jti") == []


def test_in_app_to_another_orgs_user_answers_like_a_user_that_does_not_exist(world: World) -> None:
    answers = []
    for user in ("u_globex_hr1", "u_nobody"):
        with pytest.raises(SinkRefusedError) as refused:
            world.send(None, {"channel": "in_app", "recipient_user_id": user, "body": "Hi"})
        answers.append((refused.value.reason, str(refused.value)))
    assert answers[0] == answers[1] == ("unknown_recipient", "unknown_recipient: no such user")
    assert world.db.raw("notifications") == []


def test_in_app_refuses_a_body_carrying_a_token(world: World) -> None:
    with pytest.raises(SinkRefusedError):
        world.send(None, {"channel": "in_app", "recipient_user_id": "u_acme_hr1", "body": world.tokens["mobile"]})


def test_the_caller_org_comes_from_the_context_not_the_grant(world: World) -> None:
    # An org-B caller presenting an org-A grant for an org-A token resolves nothing.
    caller_b = Caller(org_id=ORG_B, agent="templates_agent", run_id="run_globex_001")
    with pytest.raises(SinkRefusedError) as refused:
        world.send(world.grant(), world.telegram(), caller=caller_b)
    assert refused.value.reason == "grant_rejected:wrong_org"
    assert world.db.raw("mock_outbox") == []
    assert world.transport.sent == 0
    assert {r.reason for r in world.records(ORG_B)} == {"wrong_org"}
    assert world.records(ORG_A) == []


def test_the_world_uses_distinct_hires() -> None:
    assert len({HIRE, OTHER_HIRE}) == 2
    assert CALLER.org_id == ORG_A
