"""Each tool's JSON-schema contract (PRD §7.5 tool table)."""

from __future__ import annotations

from typing import Any

import pytest
from onb_mcp_mongo.contracts import CONTRACTS, MAX_FIND_LIMIT
from pydantic import ValidationError

TOOLS = {"list_collections", "find", "count", "aggregate", "insert_one", "update_one"}


def test_there_is_a_contract_for_exactly_the_prd_tools() -> None:
    assert set(CONTRACTS) == TOOLS


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_contract_is_a_closed_object_schema(tool: str) -> None:
    schema = CONTRACTS[tool].args.model_json_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("tool", sorted(TOOLS - {"list_collections"}))
def test_every_collection_tool_requires_a_collection(tool: str) -> None:
    schema = CONTRACTS[tool].args.model_json_schema()
    assert "collection" in schema["required"]
    assert schema["properties"]["collection"]["pattern"]


def test_find_requires_a_limit_of_at_most_100() -> None:
    schema = CONTRACTS["find"].args.model_json_schema()
    assert "limit" in schema["required"]
    assert schema["properties"]["limit"]["maximum"] == MAX_FIND_LIMIT == 100
    assert schema["properties"]["limit"]["minimum"] == 1


def _validate(tool: str, args: dict[str, Any]) -> None:
    CONTRACTS[tool].args.model_validate(args)


@pytest.mark.parametrize(
    "args",
    [
        {"collection": "new_hires"},
        {"collection": "new_hires", "limit": 101},
        {"collection": "new_hires", "limit": 0},
        {"collection": "new_hires", "limit": "10"},
        {"collection": "new_hires", "limit": 10.5},
        {"collection": "new_hires", "limit": True},
    ],
)
def test_find_without_a_valid_limit_is_rejected(args: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validate("find", args)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("find", {"collection": "new_hires", "limit": 10, "org_id": "org_b"}),
        ("count", {"collection": "new_hires", "org_id": "org_b"}),
        ("insert_one", {"collection": "run_tasks", "document": {}, "org_id": "org_b"}),
        ("list_collections", {"org_id": "org_b"}),
    ],
)
def test_unknown_arguments_are_rejected(tool: str, args: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validate(tool, args)


@pytest.mark.parametrize("name", ["", "system.users", "$cmd", "a.b", "1abc", "x" * 65, "pii vault"])
def test_a_collection_name_outside_the_pattern_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        _validate("count", {"collection": name})


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("list_collections", {}),
        ("find", {"collection": "new_hires", "limit": 100}),
        (
            "find",
            {"collection": "new_hires", "filter": {"a": 1}, "projection": {"a": 1}, "sort": [["a", -1]], "limit": 1},
        ),
        ("count", {"collection": "new_hires", "filter": {}}),
        ("aggregate", {"collection": "new_hires", "pipeline": [{"$match": {}}]}),
        ("insert_one", {"collection": "run_tasks", "document": {"title": "x"}}),
        ("update_one", {"collection": "run_tasks", "filter": {"_id": 1}, "update": {"$set": {"a": 1}}}),
    ],
)
def test_well_formed_arguments_pass(tool: str, args: dict[str, Any]) -> None:
    _validate(tool, args)


@pytest.mark.parametrize("sort", [[["a", 2]], [["a"]], [["", 1]], [["a", True]], [["a", 1.0]]])
def test_a_sort_must_be_field_and_direction_pairs(sort: list[list[Any]]) -> None:
    with pytest.raises(ValidationError):
        _validate("find", {"collection": "new_hires", "limit": 1, "sort": sort})


def test_an_update_needs_a_non_empty_filter_and_update() -> None:
    with pytest.raises(ValidationError):
        _validate("update_one", {"collection": "run_tasks", "filter": {}, "update": {"$set": {"a": 1}}})
    with pytest.raises(ValidationError):
        _validate("update_one", {"collection": "run_tasks", "filter": {"_id": 1}, "update": {}})


def test_an_aggregate_needs_at_least_one_stage() -> None:
    with pytest.raises(ValidationError):
        _validate("aggregate", {"collection": "new_hires", "pipeline": []})
