"""The idempotency store: one record per (org, tool, idempotency key).

The first call with a key creates the record; every later call with the same key and the same
arguments returns that record's result and creates nothing. The same key with different arguments
is a conflict (`IdempotencyConflictError`) and changes nothing. Records are per org: one org's key
never answers another org's call.

The store is in memory: the mocks are thin (PRD §2), and because results are derived
deterministically from (org, tool, key, arguments), a replay after a restart still returns the same
result. It is bounded; when full, new records are refused but replays still answer.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "MAX_RECORDS",
    "IdempotencyConflictError",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "Outcome",
    "StoreFullError",
]

MAX_RECORDS = 100_000


class IdempotencyConflictError(Exception):
    """The key was already used with other arguments."""


class StoreFullError(Exception):
    """The store holds `max_records` records and refuses new ones."""


@dataclass(frozen=True, slots=True)
class Outcome:
    result: Mapping[str, Any]
    replayed: bool


class IdempotencyStore(Protocol):
    async def get_or_create(
        self, org_id: str, tool: str, key: str, fingerprint: str, create: Callable[[], Mapping[str, Any]]
    ) -> Outcome: ...


@dataclass(frozen=True, slots=True)
class _Record:
    fingerprint: str
    result: Mapping[str, Any]


class InMemoryIdempotencyStore:
    def __init__(self, *, max_records: int = MAX_RECORDS) -> None:
        self._records: dict[tuple[str, str, str], _Record] = {}
        self._lock = asyncio.Lock()
        self._max = max_records
        self._creations = 0

    async def get_or_create(
        self, org_id: str, tool: str, key: str, fingerprint: str, create: Callable[[], Mapping[str, Any]]
    ) -> Outcome:
        """The stored result for the key, or a new one from `create()`. Callers get a copy."""
        async with self._lock:
            record = self._records.get((org_id, tool, key))
            if record is not None:
                if record.fingerprint != fingerprint:
                    raise IdempotencyConflictError("the idempotency key was used with other arguments")
                return Outcome(copy.deepcopy(dict(record.result)), replayed=True)
            if len(self._records) >= self._max:
                raise StoreFullError("the mock store is full")
            result = copy.deepcopy(dict(create()))
            self._records[(org_id, tool, key)] = _Record(fingerprint, result)  # private, under the lock
            self._creations += 1
            return Outcome(copy.deepcopy(result), replayed=False)

    def count(self, org_id: str | None = None) -> int:
        """Records held, in one org or in all."""
        return sum(1 for org, _, _ in self._records if org_id is None or org == org_id)

    @property
    def creations(self) -> int:
        """How many times a record was created (a replay never adds one)."""
        return self._creations
