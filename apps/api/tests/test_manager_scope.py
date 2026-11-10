"""Managers only see what belongs to their direct reports: `manager_id == sub` (PRD §7.8)."""

import pytest
from onb_api.auth.scope import manager_filter, require_in_manager_scope
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_contracts import ErrorCode
from onb_contracts.auth import Role

RAVI = Principal(user_id="u_acme_mgr_ravi", org_id="org_acme", role=Role.MANAGER)
HR = Principal(user_id="u_acme_hr1", org_id="org_acme", role=Role.HR)
ADMIN = Principal(user_id="u_acme_hradmin", org_id="org_acme", role=Role.HR_ADMIN)


def test_a_manager_filter_narrows_to_their_reports() -> None:
    assert manager_filter(RAVI) == {"manager_id": "u_acme_mgr_ravi"}


@pytest.mark.parametrize("principal", [HR, ADMIN])
def test_hr_roles_are_not_narrowed(principal: Principal) -> None:
    assert manager_filter(principal) == {}


def test_a_manager_may_see_their_report() -> None:
    require_in_manager_scope(RAVI, {"_id": "h_acme_001", "manager_id": "u_acme_mgr_ravi"})


@pytest.mark.parametrize("record", [{"manager_id": "u_acme_mgr_anita"}, {}, {"manager_id": None}])
def test_another_managers_report_is_not_found_not_forbidden(record: dict[str, object]) -> None:
    with pytest.raises(AppError) as caught:
        require_in_manager_scope(RAVI, record)
    assert caught.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("principal", [HR, ADMIN])
def test_hr_roles_see_every_record(principal: Principal) -> None:
    require_in_manager_scope(principal, {"manager_id": "u_acme_mgr_anita"})
