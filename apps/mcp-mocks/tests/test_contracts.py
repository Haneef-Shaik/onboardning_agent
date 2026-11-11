"""The closed JSON-schema contracts of the mock tools (the hub checks these before forwarding)."""

from __future__ import annotations

from typing import Any

import pytest
from onb_mcp_mocks.contracts import CONTRACTS, TOOL_NAMES, CreateBitbucketAccountArgs
from pydantic import ValidationError

from .harness import BITBUCKET_ARGS, LAPTOP_ARGS

VALID = {"create_bitbucket_account": BITBUCKET_ARGS, "provision_laptop": LAPTOP_ARGS}


def test_the_tools_are_the_ones_the_provisioning_manifest_names() -> None:
    """REG-1's config/agents/provisioning_agent.yaml grants `mocks: [create_bitbucket_account, provision_laptop]`."""
    assert TOOL_NAMES == ("create_bitbucket_account", "provision_laptop")
    assert sorted(CONTRACTS) == list(TOOL_NAMES)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_schema_is_closed_and_needs_an_idempotency_key(name: str) -> None:
    schema = CONTRACTS[name].args.model_json_schema(by_alias=True)
    assert schema["additionalProperties"] is False
    assert "task_id" in schema["required"]
    assert "idempotency" in schema["properties"]["task_id"]["description"].lower()
    assert CONTRACTS[name].read_only is False


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize(
    "change",
    [
        {"org_id": "org_b"},
        {"task_id": "not-a-hex-id"},
        {"task_id": "65F0A1B2C3D4E5F6A7B8C9D0"},
        {"hire_id": "x"},
        {"hire_id": "ptk_01JDXXXXXXXXXXXXXXXXXXXXXX"},
    ],
)
def test_bad_or_extra_arguments_are_refused(name: str, change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CONTRACTS[name].args.model_validate({**VALID[name], **change})


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "Acme Eng"},
        {"workspace": ""},
        {"groups": ["a"] * 11},
        {"groups": ["Back End"]},
        {"groups": ["x", "x"]},
    ],
)
def test_bitbucket_arguments_are_plain_slugs(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CONTRACTS["create_bitbucket_account"].args.model_validate({**BITBUCKET_ARGS, **change})


@pytest.mark.parametrize("change", [{"profile": "gaming"}, {"office": "Bangalore, MG Road"}, {"office": ""}])
def test_laptop_arguments_are_closed(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CONTRACTS["provision_laptop"].args.model_validate({**LAPTOP_ARGS, **change})


def test_groups_default_to_none() -> None:
    args = {k: v for k, v in BITBUCKET_ARGS.items() if k != "groups"}
    assert CreateBitbucketAccountArgs.model_validate(args).groups == []
