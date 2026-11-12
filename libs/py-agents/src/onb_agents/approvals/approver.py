"""Who decides an approval (PRD FR-H1, template `approver` rule).

A template task names its approver by rule: a `user_ref` variable (the hire's `manager_id`, the only
user reference a hire record holds), a platform role (any user with it may decide), or one user id. A
task that waits for a human without a rule (an agent said `needs_human`, or a tool call needs approval)
goes to the hire's manager, and when the hire has none to HR. The result is `(approver_id, approver_role)`:
exactly one is set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

__all__ = ["FALLBACK_ROLE", "resolve_approver"]

FALLBACK_ROLE: Final = "HR"
_HIRE_USER_REFS: Final = ("manager_id",)


def _hire_user(hire: Mapping[str, Any], name: str) -> str | None:
    value = hire.get(name) if name in _HIRE_USER_REFS else None
    return value if isinstance(value, str) and value else None


def resolve_approver(approver: Mapping[str, str] | None, hire: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The approver of a task: `(user id, None)` or `(None, role)`."""
    if approver is not None:
        rule, ref = approver.get("rule"), approver.get("ref", "")
        if rule == "user" and ref:
            return ref, None
        if rule == "role" and ref:
            return None, ref
        if rule == "variable":
            user = _hire_user(hire, ref)
            if user is not None:
                return user, None
    manager = _hire_user(hire, "manager_id")
    if manager is not None:
        return manager, None
    return None, FALLBACK_ROLE
