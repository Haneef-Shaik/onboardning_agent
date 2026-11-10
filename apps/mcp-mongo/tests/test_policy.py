"""Argument rules mcp-mongo applies before any database call (PRD §7.5).

The full operator validator is MCP-2. These are the rules MCP-1 owns: `org_id` is never chosen by
the agent, collections come from the call context, and aggregate stages are allowlisted.
"""

from __future__ import annotations

import pytest
from bson import ObjectId
from onb_mcp_mongo.call_context import CallContext
from onb_mcp_mongo.policy import (
    ALLOWED_STAGES,
    ToolRejectedError,
    check_pipeline,
    check_readable,
    check_writable,
    decode_ids,
    reject_org_id,
)

CTX = CallContext(
    org_id="org_a",
    agent="db_agent",
    run_id="run_1",
    tool="find",
    read=frozenset({"new_hires"}),
    write=frozenset({"run_tasks"}),
)


@pytest.mark.parametrize(
    "value",
    [
        {"org_id": "org_b"},
        {"org_id": "org_a"},
        {"org_id.x": 1},
        {"$and": [{"name": "a"}, {"org_id": "org_b"}]},
        {"$or": [{"$nor": [{"org_id": {"$ne": "org_a"}}]}]},
        {"profile": {"org_id": "org_b"}},
        {"$set": {"org_id": "org_b"}},
        {"$unset": {"org_id": ""}},
        {"$rename": {"x": "org_id"}},
        [{"$match": {"org_id": "org_b"}}],
        [{"$addFields": {"org_id": "org_b"}}],
    ],
)
def test_org_id_anywhere_is_rejected(value: object) -> None:
    with pytest.raises(ToolRejectedError) as caught:
        reject_org_id(value, "filter")
    assert caught.value.reason == "org_id_not_allowed"


@pytest.mark.parametrize("value", [{}, {"name": "a"}, {"organisation": 1}, {"$and": [{"x": {"$gt": 1}}]}, None])
def test_values_without_org_id_pass(value: object) -> None:
    reject_org_id(value, "filter")


def test_the_rejection_names_the_argument_but_not_the_value() -> None:
    with pytest.raises(ToolRejectedError) as caught:
        reject_org_id({"org_id": "secret-org-value"}, "filter")
    assert "filter" in str(caught.value)
    assert "secret-org-value" not in str(caught.value)


def test_readable_collections_come_from_the_context() -> None:
    check_readable(CTX, "new_hires")
    check_readable(CTX, "run_tasks")  # writable implies readable
    with pytest.raises(ToolRejectedError) as caught:
        check_readable(CTX, "pii_vault")
    assert caught.value.reason == "collection_not_allowed"


def test_writable_collections_come_from_the_context() -> None:
    check_writable(CTX, "run_tasks")
    with pytest.raises(ToolRejectedError) as caught:
        check_writable(CTX, "new_hires")
    assert caught.value.reason == "collection_not_allowed"


def test_the_allowed_stages_are_exactly_the_prd_list() -> None:
    assert frozenset({"$match", "$project", "$group", "$sort", "$limit", "$count", "$unwind", "$addFields"}) == (
        ALLOWED_STAGES
    )


def test_an_allowlisted_pipeline_passes() -> None:
    check_pipeline([{"$match": {"status": "open"}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}])


@pytest.mark.parametrize("stage", ["$lookup", "$out", "$merge", "$unionWith", "$graphLookup", "$facet", "$set"])
def test_a_stage_off_the_allowlist_is_rejected(stage: str) -> None:
    with pytest.raises(ToolRejectedError) as caught:
        check_pipeline([{"$match": {}}, {stage: {}}])
    assert caught.value.reason == "stage_not_allowed"


@pytest.mark.parametrize("pipeline", [[{}], [{"$match": {}, "$sort": {"a": 1}}], [{"match": {}}]])
def test_a_malformed_stage_is_rejected(pipeline: list[dict[str, object]]) -> None:
    with pytest.raises(ToolRejectedError) as caught:
        check_pipeline(pipeline)
    assert caught.value.reason == "invalid_stage"


def test_object_ids_are_decoded_from_extended_json() -> None:
    oid = ObjectId()
    decoded = decode_ids({"_id": {"$oid": str(oid)}, "tags": [{"$oid": str(oid)}], "x": {"$in": [1]}})
    assert decoded == {"_id": oid, "tags": [oid], "x": {"$in": [1]}}


def test_only_object_ids_are_decoded() -> None:
    # Other extended-JSON forms (e.g. $code) stay plain data, so they never become BSON code.
    value = {"a": {"$code": "function () {}"}, "b": {"$oid": "not-hex"}, "c": {"$oid": str(ObjectId()), "x": 1}}
    assert decode_ids(value) == value


def test_decoding_does_not_change_its_input() -> None:
    oid = str(ObjectId())
    original = {"_id": {"$oid": oid}}
    decode_ids(original)
    assert original == {"_id": {"$oid": oid}}
