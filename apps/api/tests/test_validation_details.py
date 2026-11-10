"""`safe_details`: declared field paths and fixed issue texts only (no submitted values or keys)."""

import pytest
from onb_api.validation import GENERIC_ISSUE, issue_for, safe_details
from pydantic import BaseModel, Field


class Item(BaseModel):
    sku: str


class Order(BaseModel):
    items: list[Item]
    note: Item | None = None
    extras: dict[str, Item] = Field(default_factory=dict)


def _field(loc: tuple[object, ...], error_type: str = "missing", body: object = Order) -> str | None:
    return safe_details([{"loc": loc, "type": error_type, "msg": "x", "input": "x"}], body)[0].field


@pytest.mark.parametrize(
    ("loc", "error_type", "expected"),
    [
        (("body", "items", 2, "sku"), "missing", "body.items.2.sku"),
        (("body", "note", "sku"), "string_type", "body.note.sku"),
        (("body", "extras", "jane@example.com", "sku"), "missing", "body.extras.*.sku"),
        (("body", "items", "oops"), "missing", "body.items.*"),
        (("body", "unknown-key"), "extra_forbidden", "body.<extra>"),
        (("body", 17), "json_invalid", "body"),
        (("query", "limit"), "int_parsing", "query.limit"),
        (("header",), "missing", "header"),
        ((), "missing", None),
        (("weird", "x"), "missing", "weird"),
    ],
)
def test_locations_keep_only_declared_segments(loc: tuple[object, ...], error_type: str, expected: str | None) -> None:
    assert _field(loc, error_type) == expected


def test_without_a_body_type_every_body_segment_is_a_wildcard() -> None:
    assert _field(("body", "a", "b"), body=None) == "body.*.*"


@pytest.mark.parametrize(
    ("error_type", "issue"),
    [
        ("missing", "Field required"),
        ("int_parsing", "Wrong type"),
        ("string_type", "Wrong type"),
        ("string_too_long", "Wrong length"),
        ("greater_than_equal", "Out of range"),
        ("string_pattern_mismatch", "Wrong format"),
        ("value_error", GENERIC_ISSUE),
        ("something_new", GENERIC_ISSUE),
    ],
)
def test_issue_texts_come_from_a_fixed_table(error_type: str, issue: str) -> None:
    assert issue_for(error_type) == issue
