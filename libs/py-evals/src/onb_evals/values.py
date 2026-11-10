"""JSON value comparisons for checks: strict about types (`true` is not `1`), deep for arrays and objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["json_equal", "subset_match"]


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def json_equal(left: Any, right: Any) -> bool:
    """Deep equality with JSON types: a bool never equals a number, an array never equals a string."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if _is_array(left) and _is_array(right):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, Mapping) or isinstance(right, Mapping) or _is_array(left) or _is_array(right):
        return False
    return bool(left == right)


def subset_match(actual: Any, expected: Any) -> bool:
    """`expected` is a subset of `actual`: every key it names matches (recursively); other values are equal."""
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and subset_match(actual[key], value) for key, value in expected.items()
        )
    return json_equal(actual, expected)
