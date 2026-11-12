"""The approval SLA (PRD FR-H4; HIL-2): when an undecided approval reminds its approver, and when it goes to HR.

An approval's SLA is its `sla_hours` (from the template task), or the configured default when it has none
(tool-call and `needs_human` approvals). Two thresholds, both multiples of that SLA and both from
`config/approval_sla.yaml` (ADR 0027):

* `remind_after_sla` (1.0: the moment it is overdue): the approver is reminded, **once**;
* `escalate_after_sla` (2.0): the approval is escalated to the `escalate_to_role` users (HR), **once**.

`due_stages` is pure: it reads `status`, `requested_at`, `sla_hours`, `reminded_at` and `escalated_at`
and says which stages are owed now, in order. A stage that has a timestamp is never owed again, so the
record itself is what stops a repeat (the sweep sets the timestamp in the same transaction that publishes
the stage's event; see `onb_orchestrator.approval_sla`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from onb_contracts.auth import Role

__all__ = [
    "DEFAULT_SLA_CONFIG",
    "SLA_STAGES",
    "SlaConfigError",
    "SlaPolicy",
    "SlaStage",
    "due_stages",
    "load_sla_policy",
    "stage_field",
]

SlaStage = Literal["reminder", "escalation"]
SLA_STAGES: Final[tuple[SlaStage, ...]] = ("reminder", "escalation")
DEFAULT_SLA_CONFIG: Final = Path("config/approval_sla.yaml")
_STAGE_FIELDS: Final[Mapping[SlaStage, str]] = {"reminder": "reminded_at", "escalation": "escalated_at"}
_ESCALATION_ROLES: Final = frozenset({Role.HR.value, Role.HR_ADMIN.value})
_MAX_SLA_HOURS: Final = 24 * 365  # the template's own bound on `sla_hours`
_MAX_MULTIPLE: Final = 100.0
_MAX_INTERVAL_SECONDS: Final = 24 * 3600.0


class SlaConfigError(ValueError):
    """The SLA config file is missing or malformed: startup fails fast."""


def stage_field(stage: SlaStage) -> str:
    """The approval field that records when the stage was done."""
    return _STAGE_FIELDS[stage]


@dataclass(frozen=True, slots=True)
class SlaPolicy:
    """The thresholds. The defaults are `config/approval_sla.yaml`'s values (a test keeps them equal)."""

    default_sla_hours: int = 24
    remind_after_sla: float = 1.0
    escalate_after_sla: float = 2.0
    escalate_to_role: str = Role.HR.value
    check_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if isinstance(self.default_sla_hours, bool) or not isinstance(self.default_sla_hours, int):
            raise SlaConfigError("default_sla_hours must be a whole number of hours")
        if not 0 < self.default_sla_hours <= _MAX_SLA_HOURS:
            raise SlaConfigError(f"default_sla_hours must be 1-{_MAX_SLA_HOURS}")
        for name in ("remind_after_sla", "escalate_after_sla"):
            _check_number(name, getattr(self, name), high=_MAX_MULTIPLE)
        _check_number("check_interval_seconds", self.check_interval_seconds, high=_MAX_INTERVAL_SECONDS)
        if self.escalate_after_sla <= self.remind_after_sla:
            raise SlaConfigError("escalate_after_sla must be above remind_after_sla (remind, then escalate)")
        if self.escalate_to_role not in _ESCALATION_ROLES:
            raise SlaConfigError(f"escalate_to_role must be one of {sorted(_ESCALATION_ROLES)}")

    def threshold(self, stage: SlaStage) -> float:
        return self.remind_after_sla if stage == "reminder" else self.escalate_after_sla


def _check_number(name: str, value: object, *, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SlaConfigError(f"{name} must be a number")
    if not math.isfinite(value) or not 0 < value <= high:
        raise SlaConfigError(f"{name} must be a number above 0 and at most {high:g}")


def load_sla_policy(path: Path) -> SlaPolicy:
    """Read the thresholds from `path`. Missing keys take the defaults; unknown keys are refused."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        raise SlaConfigError(f"cannot read the approval SLA config {path}") from None
    except yaml.YAMLError:
        raise SlaConfigError(f"the approval SLA config {path} is not valid YAML") from None
    if not isinstance(raw, Mapping):
        raise SlaConfigError("the approval SLA config must be a mapping of settings")
    known = {f.name for f in fields(SlaPolicy)}
    unknown = sorted(str(key) for key in raw if key not in known)
    if unknown:
        raise SlaConfigError(f"unknown approval SLA setting(s): {', '.join(unknown)}")
    values = dict(raw)
    for name in ("remind_after_sla", "escalate_after_sla", "check_interval_seconds"):
        if isinstance(values.get(name), int) and not isinstance(values.get(name), bool):
            values[name] = float(values[name])
    return SlaPolicy(**values)


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _sla_hours(approval: Mapping[str, Any], policy: SlaPolicy) -> int:
    value = approval.get("sla_hours")
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _MAX_SLA_HOURS:
        return value
    return policy.default_sla_hours


def due_stages(approval: Mapping[str, Any], now: datetime, policy: SlaPolicy) -> tuple[SlaStage, ...]:
    """The stages owed now, reminder before escalation. Empty unless the approval is still `pending`."""
    requested_at = _aware(approval.get("requested_at"))
    if approval.get("status") != "pending" or requested_at is None:
        return ()
    age = now - requested_at
    sla = timedelta(hours=_sla_hours(approval, policy))
    return tuple(
        stage
        for stage in SLA_STAGES
        if approval.get(stage_field(stage)) is None and age >= sla * policy.threshold(stage)
    )
