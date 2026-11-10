"""Dot paths into an agent's output or a stored document (evals/README.md "Check catalog").

`resolve(doc, "packet.risk_flags")` walks mapping keys; a numeric segment indexes a list. A path that
does not exist gives `MISSING`, which is distinct from a present `null`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, final

__all__ = ["MISSING", "Missing", "resolve", "split_path"]


@final
class Missing:
    """The value of a path that does not exist."""

    _instance: Missing | None = None

    def __new__(cls) -> Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = Missing()


def split_path(path: str) -> tuple[str, ...]:
    parts = tuple(path.split("."))
    if not path or any(not part for part in parts):
        raise ValueError(f"malformed path {path!r}: use dot-separated keys such as 'packet.risk_flags'")
    return parts


def _step(node: Any, part: str) -> Any:
    if isinstance(node, Mapping):
        return node.get(part, MISSING)
    if isinstance(node, Sequence) and not isinstance(node, str | bytes) and part.isdigit():
        index = int(part)
        return node[index] if index < len(node) else MISSING
    return MISSING


def resolve(document: Any, path: str) -> Any:
    """The value at `path`, or `MISSING`."""
    node = document
    for part in split_path(path):
        node = _step(node, part)
        if node is MISSING:
            return MISSING
    return node
