"""The per-task agent token and the out-of-band tool context (PRD §7.4, §7.10)."""

from __future__ import annotations

import secrets

import pytest
from onb_agents.registry import load_manifests
from onb_agents.runtime.task import (
    GRANT_HEADER,
    AgentTask,
    hub_headers,
    hub_tool_name,
    issue_task_token,
    task_scopes,
)
from onb_agents.tokens import verify_agent_token

from .support import AGENTS_DIR

KEY = secrets.token_urlsafe(40)
MANIFESTS = {m.name: m for m in load_manifests(AGENTS_DIR)}
DB = MANIFESTS["db_agent"]


def task(**kw: object) -> AgentTask:
    base: dict[str, object] = {"org_id": "org_acme", "run_id": "run_1", "task_id": "t1", "input": {"message": "hi"}}
    return AgentTask(**{**base, **kw})  # type: ignore[arg-type]


def test_hub_tool_names_are_server_double_underscore_tool() -> None:
    assert hub_tool_name("mongo", "find") == "mongo__find"


def test_the_scopes_are_the_manifests_hub_tools_and_no_approval_by_default() -> None:
    assert task_scopes(DB, ()) == ("mongo__find", "mongo__aggregate", "mongo__count", "mongo__update_one")
    assert task_scopes(MANIFESTS["templates_agent"], ()) == ("mongo__find", "notify__notify_send")


def test_an_approval_is_added_only_for_a_rule_the_manifest_requires() -> None:
    manifest = DB.model_copy(update={"requires_approval": ("update_one:run_tasks",)})
    assert task_scopes(manifest, ("update_one:run_tasks",))[-1] == "approved:update_one:run_tasks"
    assert not any(s.startswith("approved:") for s in task_scopes(manifest, ()))
    with pytest.raises(ValueError, match="not a rule"):
        task_scopes(manifest, ("update_one:new_hires",))


def test_the_task_token_carries_this_task_and_verifies_at_the_hub() -> None:
    token = issue_task_token(DB, task(), KEY)
    claims = verify_agent_token(token, KEY).claims
    assert (claims.agent, claims.org_id, claims.run_id) == ("db_agent", "org_acme", "run_1")
    assert claims.scopes == task_scopes(DB, ())


def test_each_task_gets_its_own_token() -> None:
    first = verify_agent_token(issue_task_token(DB, task(), KEY), KEY)
    second = verify_agent_token(issue_task_token(DB, task(), KEY), KEY)
    assert first.jti != second.jti


def test_the_grant_travels_only_in_a_header_and_never_in_the_tasks_repr() -> None:
    grant = "eyJhbGciOiJFZERTQSJ9." + secrets.token_urlsafe(32)
    with_grant = task(grant=grant)
    assert grant not in repr(with_grant)
    assert grant not in str(with_grant)
    headers = hub_headers("t0ken", with_grant.grant)
    assert headers == {"Authorization": "Bearer t0ken", GRANT_HEADER: grant}
    assert hub_headers("t0ken", None) == {"Authorization": "Bearer t0ken"}


@pytest.mark.parametrize("field", ["org_id", "run_id", "task_id"])
def test_task_ids_are_checked(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        task(**{field: "bad id!"})
