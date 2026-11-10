"""Dot paths into an agent's output (evals/README.md: "Paths use dot notation into the agent output")."""

from __future__ import annotations

import pytest
from onb_evals.paths import MISSING, resolve


def test_a_nested_key_resolves() -> None:
    assert resolve({"packet": {"recommendation": "approve"}}, "packet.recommendation") == "approve"


def test_a_numeric_segment_indexes_a_list() -> None:
    assert resolve({"citations": [{"run_id": "r1"}]}, "citations.0.run_id") == "r1"


@pytest.mark.parametrize(
    ("document", "path"),
    [
        ({}, "status"),
        ({"packet": None}, "packet.summary"),
        ({"packet": "text"}, "packet.summary"),
        ({"items": [1]}, "items.3"),
        ({"items": [1]}, "items.x"),
        (None, "status"),
    ],
)
def test_a_path_that_does_not_exist_is_missing(document: object, path: str) -> None:
    assert resolve(document, path) is MISSING


def test_a_present_null_is_not_missing() -> None:
    assert resolve({"found": None}, "found") is None


@pytest.mark.parametrize("path", ["", ".a", "a..b", "a."])
def test_a_malformed_path_is_refused(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        resolve({}, path)


def test_missing_is_falsy_and_named() -> None:
    assert not MISSING
    assert repr(MISSING) == "MISSING"
