"""The approval SLA policy: thresholds from config, and which stage an approval is due for (HIL-2, FR-H4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from onb_agents.approvals.sla import (
    DEFAULT_SLA_CONFIG,
    SlaConfigError,
    SlaPolicy,
    due_stages,
    load_sla_policy,
)

REQUESTED = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


def _approval(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_id": "appr_1",
        "status": "pending",
        "requested_at": REQUESTED,
        "sla_hours": 10,
        "reminded_at": None,
        "escalated_at": None,
    }
    return {**base, **changes}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "approval_sla.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- thresholds come from config --------------------------------------------------------------------------


def test_the_thresholds_come_from_the_config_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "default_sla_hours: 6\nremind_after_sla: 1.5\nescalate_after_sla: 3\n"
        "escalate_to_role: HR_ADMIN\ncheck_interval_seconds: 30\n",
    )
    policy = load_sla_policy(path)
    assert policy == SlaPolicy(
        default_sla_hours=6,
        remind_after_sla=1.5,
        escalate_after_sla=3.0,
        escalate_to_role="HR_ADMIN",
        check_interval_seconds=30.0,
    )
    # and the stages follow them: a 10 h SLA reminds at 15 h, escalates at 30 h
    assert due_stages(_approval(), REQUESTED + timedelta(hours=14, minutes=59), policy) == ()
    assert due_stages(_approval(), REQUESTED + timedelta(hours=15), policy) == ("reminder",)
    assert due_stages(_approval(), REQUESTED + timedelta(hours=30), policy) == ("reminder", "escalation")


def test_a_changed_config_file_changes_when_an_approval_is_due(tmp_path: Path) -> None:
    now = REQUESTED + timedelta(hours=12)
    early = load_sla_policy(_write(tmp_path, "remind_after_sla: 1\nescalate_after_sla: 1.2\n"))
    late = load_sla_policy(_write(tmp_path, "remind_after_sla: 2\nescalate_after_sla: 4\n"))
    assert due_stages(_approval(), now, early) == ("reminder", "escalation")
    assert due_stages(_approval(), now, late) == ()


def test_the_shipped_config_file_loads() -> None:
    policy = load_sla_policy(ROOT / DEFAULT_SLA_CONFIG)
    assert policy.remind_after_sla < policy.escalate_after_sla
    assert policy.escalate_to_role == "HR"
    assert policy == SlaPolicy()  # the code's defaults are the shipped file's values


def test_a_missing_key_takes_the_default(tmp_path: Path) -> None:
    assert load_sla_policy(_write(tmp_path, "default_sla_hours: 12\n")) == SlaPolicy(default_sla_hours=12)


@pytest.mark.parametrize(
    "text",
    [
        "remind_after_sla: 2\nescalate_after_sla: 2\n",  # escalation must come after the reminder
        "remind_after_sla: 3\nescalate_after_sla: 2\n",
        "remind_after_sla: 0\n",
        "remind_after_sla: -1\n",
        "default_sla_hours: 0\n",
        "default_sla_hours: 1.5\n",
        "default_sla_hours: true\n",
        "remind_after_sla: soon\n",
        "remind_after_sla: .nan\n",
        "escalate_after_sla: .inf\n",
        "escalate_to_role: CEO\n",
        "check_interval_seconds: 0\n",
        "surprise: 1\n",  # an unknown key is a typo, not ignored
        "- a list\n",
        "",
    ],
)
def test_a_bad_config_fails_fast(tmp_path: Path, text: str) -> None:
    with pytest.raises(SlaConfigError):
        load_sla_policy(_write(tmp_path, text))


def test_a_missing_config_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(SlaConfigError):
        load_sla_policy(tmp_path / "nope.yaml")


# --- which stage is due -----------------------------------------------------------------------------------

POLICY = SlaPolicy(default_sla_hours=24, remind_after_sla=1.0, escalate_after_sla=2.0)


def test_an_approval_inside_its_sla_is_not_due() -> None:
    assert due_stages(_approval(), REQUESTED + timedelta(hours=9, minutes=59), POLICY) == ()


def test_an_overdue_approval_is_due_a_reminder_then_an_escalation() -> None:
    assert due_stages(_approval(), REQUESTED + timedelta(hours=10), POLICY) == ("reminder",)
    assert due_stages(_approval(), REQUESTED + timedelta(hours=19), POLICY) == ("reminder",)
    reminded = _approval(reminded_at=REQUESTED + timedelta(hours=10))
    assert due_stages(reminded, REQUESTED + timedelta(hours=19), POLICY) == ()
    assert due_stages(reminded, REQUESTED + timedelta(hours=20), POLICY) == ("escalation",)


def test_a_stage_already_done_is_never_due_again() -> None:
    done = _approval(reminded_at=REQUESTED, escalated_at=REQUESTED)
    assert due_stages(done, REQUESTED + timedelta(days=30), POLICY) == ()


def test_only_a_pending_approval_is_due() -> None:
    for status in ("approved", "rejected", "cancelled"):
        assert due_stages(_approval(status=status), REQUESTED + timedelta(days=3), POLICY) == ()


def test_an_approval_without_an_sla_takes_the_configured_default() -> None:
    no_sla = _approval(sla_hours=None)
    assert due_stages(no_sla, REQUESTED + timedelta(hours=23), POLICY) == ()
    assert due_stages(no_sla, REQUESTED + timedelta(hours=24), POLICY) == ("reminder",)


def test_a_naive_requested_at_is_read_as_utc() -> None:
    naive = _approval(requested_at=REQUESTED.replace(tzinfo=None))
    assert due_stages(naive, REQUESTED + timedelta(hours=10), POLICY) == ("reminder",)


def test_a_record_without_a_usable_requested_at_is_never_due() -> None:
    assert due_stages(_approval(requested_at=None), REQUESTED + timedelta(days=3), POLICY) == ()
    assert due_stages(_approval(requested_at="yesterday"), REQUESTED + timedelta(days=3), POLICY) == ()
