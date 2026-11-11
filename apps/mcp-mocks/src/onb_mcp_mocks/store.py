"""The idempotency store: one record per (org, tool, idempotency key).

The first call with a key creates the record; every later call with the same key and the same
arguments returns that record's result and creates nothing. The same key with different arguments
is a conflict (`IdempotencyConflictError`) and changes nothing. Records are per org: one org's key
never answers another org's call.

The store is in memory: the mocks are thin (PRD §2). Results are derived deterministically from
(org, tool, key, arguments), so after a restart the same call still gets the same ids, and the same key
with other arguments gets other ids; only the conflict refusal is lost across a restart. Each org has
its own quota, so one org filling it never blocks another; a full org's replays still answer.

A `threading.Lock` (not an `asyncio.Lock`) guards the records: `create()` is synchronous, so the
critical section never awaits, and the store works under any event loop.
"""

from __future__ import annotations

import copy
import threading
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "MAX_RECORDS_PER_ORG",
    "IdempotencyConflictError",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "Outcome",
    "StoreFullError",
]

MAX_RECORDS_PER_ORG = 10_000


class IdempotencyConflictError(Exception):
    """The key was already used with other arguments."""


class StoreFullError(Exception):
    """The org holds `max_records_per_org` records and may create no more."""


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
    def __init__(self, *, max_records_per_org: int = MAX_RECORDS_PER_ORG) -> None:
        self._records: dict[tuple[str, str, str], _Record] = {}
        self._per_org: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._max = max_records_per_org
        self._creations = 0

    async def get_or_create(
        self, org_id: str, tool: str, key: str, fingerprint: str, create: Callable[[], Mapping[str, Any]]
    ) -> Outcome:
        """The stored result for the key, or a new one from `create()`. Callers get a copy."""
        with self._lock:
            record = self._records.get((org_id, tool, key))
            if record is not None:
                if record.fingerprint != fingerprint:
                    raise IdempotencyConflictError("the idempotency key was used with other arguments")
                return Outcome(copy.deepcopy(dict(record.result)), replayed=True)
            if self._per_org[org_id] >= self._max:
                raise StoreFullError("the org's mock records are full")
            result = copy.deepcopy(dict(create()))
            self._records[(org_id, tool, key)] = _Record(fingerprint, result)  # private, under the lock
            self._per_org[org_id] += 1
            self._creations += 1
            return Outcome(copy.deepcopy(result), replayed=False)

    def count(self, org_id: str | None = None) -> int:
        """Records held, in one org or in all."""
        with self._lock:
            return sum(self._per_org.values()) if org_id is None else self._per_org[org_id]

    @property
    def creations(self) -> int:
        """How many times a record was created (a replay never adds one)."""
        return self._creations
