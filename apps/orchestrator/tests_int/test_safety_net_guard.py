"""The T-2 tenant safety net is on for every orchestrator integration test (PRD §7.9 Safety net).

The orchestrator's real traffic (publish, claim, run and task moves, checkpoints) runs under the guard
in `test_runs_against_mongo.py`, so those tests passing proves every command carries `org_id`.
"""

from __future__ import annotations

import pytest
from onb_data.safety_net import TenantCommandListener
from pymongo import monitoring

GUARD_FIXTURE = "tenant_safety_net_guard"


def test_every_orchestrator_integration_test_runs_under_the_safety_net_guard(request: pytest.FixtureRequest) -> None:
    items = request.session.items
    assert items, "the session collected integration tests"
    unguarded = [item.nodeid for item in items if GUARD_FIXTURE not in getattr(item, "fixturenames", ())]
    assert unguarded == []


def test_the_listener_is_registered_globally(tenant_safety_net: TenantCommandListener) -> None:
    assert monitoring._LISTENERS.command_listeners.count(tenant_safety_net) == 1
