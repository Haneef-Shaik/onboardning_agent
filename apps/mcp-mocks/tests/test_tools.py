"""The mock tools: deterministic, idempotent per (org, tool, task id), isolated per org (MOCK-1 box 2)."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest
from onb_mcp_mocks import tools
from onb_mcp_mocks.contracts import CONTRACTS, BitbucketAccount
from onb_mcp_mocks.store import MAX_RECORDS, IdempotencyConflictError, InMemoryIdempotencyStore, StoreFullError

from .harness import BITBUCKET_ARGS, CTX_A, LAPTOP_ARGS, TASK_1, run

CTX_B = dataclasses.replace(CTX_A, org_id="org_b")


def _invoke(store: InMemoryIdempotencyStore, name: str, args: dict[str, Any], ctx: Any = CTX_A) -> dict[str, Any]:
    parsed = CONTRACTS[name].args.model_validate(args)
    result = run(tools.invoke(store, dataclasses.replace(ctx, tool=name), name, parsed))
    return result.model_dump(mode="json")


def test_a_bitbucket_account_is_created_once_and_described() -> None:
    store = InMemoryIdempotencyStore()
    account = _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    assert account["status"] == "active"
    assert account["workspace"] == "acme-eng"
    assert account["groups"] == ["backend"]
    assert account["hire_id"] == BITBUCKET_ARGS["hire_id"]
    assert re.fullmatch(r"[0-9a-f]{24}", account["account_id"])
    assert re.fullmatch(r"nh-[a-p]{10}", account["username"])  # never digits: nothing a PII detector matches
    assert store.count() == 1


def test_a_laptop_order_is_created_once_and_described() -> None:
    store = InMemoryIdempotencyStore()
    order = _invoke(store, "provision_laptop", LAPTOP_ARGS)
    assert (order["status"], order["profile"], order["office"]) == ("ordered", "engineering", "bangalore")
    assert order["model"]
    assert order["eta_days"] > 0
    assert re.fullmatch(r"[0-9a-f]{24}", order["order_id"])
    assert store.count() == 1


@pytest.mark.parametrize(
    ("name", "args"), [("create_bitbucket_account", BITBUCKET_ARGS), ("provision_laptop", LAPTOP_ARGS)]
)
def test_replaying_with_the_same_idempotency_key_returns_the_same_result_and_creates_nothing_new(
    name: str, args: dict[str, Any]
) -> None:
    store = InMemoryIdempotencyStore()
    first = _invoke(store, name, args)
    again = _invoke(store, name, args)
    assert again == first
    assert store.count() == 1
    assert store.creations == 1


@pytest.mark.parametrize(
    ("name", "args"), [("create_bitbucket_account", BITBUCKET_ARGS), ("provision_laptop", LAPTOP_ARGS)]
)
def test_results_are_deterministic_across_restarts(name: str, args: dict[str, Any]) -> None:
    """A new process (a fresh store) answers the same call with the same ids: the mock never randomises."""
    assert _invoke(InMemoryIdempotencyStore(), name, args) == _invoke(InMemoryIdempotencyStore(), name, args)


def test_a_new_idempotency_key_creates_a_new_record() -> None:
    store = InMemoryIdempotencyStore()
    first = _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    second = _invoke(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "task_id": "65f0a1b2c3d4e5f6a7b8c9ff"})
    assert first["account_id"] != second["account_id"]
    assert store.count() == 2


def test_the_same_key_with_different_arguments_is_a_conflict_and_changes_nothing() -> None:
    store = InMemoryIdempotencyStore()
    first = _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    with pytest.raises(IdempotencyConflictError):
        _invoke(store, "create_bitbucket_account", {**BITBUCKET_ARGS, "groups": ["admins"]})
    assert store.count() == 1
    assert _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS) == first


def test_the_same_key_in_another_org_is_another_record_with_other_ids() -> None:
    store = InMemoryIdempotencyStore()
    in_a = _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    in_b = _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS, ctx=CTX_B)
    assert in_a["account_id"] != in_b["account_id"]
    assert (store.count("org_a"), store.count("org_b")) == (1, 1)


def test_the_same_key_for_another_tool_is_another_record() -> None:
    store = InMemoryIdempotencyStore()
    _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    _invoke(store, "provision_laptop", {**LAPTOP_ARGS, "task_id": TASK_1})
    assert store.count() == 2


def test_a_returned_result_cannot_change_the_stored_one() -> None:
    store = InMemoryIdempotencyStore()
    first = run(
        tools.invoke(
            store,
            CTX_A,
            "create_bitbucket_account",
            CONTRACTS["create_bitbucket_account"].args.model_validate(BITBUCKET_ARGS),
        )
    )
    assert isinstance(first, BitbucketAccount)
    first.groups.append("admins")  # a caller mutating its copy
    assert _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)["groups"] == ["backend"]


def test_the_store_refuses_new_records_when_full() -> None:
    store = InMemoryIdempotencyStore(max_records=1)
    _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)
    with pytest.raises(StoreFullError):
        _invoke(store, "provision_laptop", LAPTOP_ARGS)
    assert _invoke(store, "create_bitbucket_account", BITBUCKET_ARGS)["status"] == "active"  # replays still answer
    assert MAX_RECORDS >= 10_000


def test_an_unknown_tool_is_refused() -> None:
    store = InMemoryIdempotencyStore()
    with pytest.raises(KeyError):
        run(
            tools.invoke(
                store, CTX_A, "drop_everything", CONTRACTS["provision_laptop"].args.model_validate(LAPTOP_ARGS)
            )
        )
