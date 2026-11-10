"""Manager scope: a MANAGER sees only records where `manager_id == sub` (PRD §3, §7.8).

`manager_filter` narrows a list query; `require_in_manager_scope` guards a single record. A record
outside the manager's scope is 404, like a foreign id, so a manager cannot learn it exists.
HR and HR_ADMIN are not narrowed (their org scope still applies through the tenant context).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onb_contracts import ErrorCode
from onb_contracts.auth import Role

from onb_api.auth.tokens import Principal
from onb_api.errors import AppError

__all__ = ["manager_filter", "require_in_manager_scope"]


def manager_filter(principal: Principal) -> dict[str, str]:
    return {"manager_id": principal.user_id} if principal.role is Role.MANAGER else {}


def require_in_manager_scope(principal: Principal, record: Mapping[str, Any]) -> None:
    if principal.role is Role.MANAGER and record.get("manager_id") != principal.user_id:
        raise AppError(ErrorCode.NOT_FOUND)
