"""The MongoDB session of the transaction the current task is running in (EVT-1; PRD §7.2 outbox).

`DataStore.run_transaction` sets it for the duration of its callback. Every repository call reads it
and passes `session=` to the driver, so a domain change and its event commit or roll back together
without callers threading a session through.

It is a contextvar, so concurrent requests never share one. A session also belongs to the task that
opened the transaction: asyncio copies context into tasks created inside the callback, but a
session is not safe for concurrent use, and a task that outlives the callback would write after the
commit. Such a task gets `TransactionError` instead of the session.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .errors import TransactionError

__all__ = ["in_transaction"]

_current: ContextVar[tuple[Any, asyncio.Task[Any] | None] | None] = ContextVar("onb_mongo_session", default=None)


def _owned_session() -> Any | None:
    bound = _current.get()
    if bound is None:
        return None
    session, owner = bound
    if owner is not asyncio.current_task():
        raise TransactionError(
            "a task started inside run_transaction cannot use its session: await the work in the callback"
        )
    return session


def in_transaction() -> bool:
    """True inside a `DataStore.run_transaction` callback, in the task that runs it."""
    bound = _current.get()
    return bound is not None and bound[1] is asyncio.current_task()


def session_kwargs() -> dict[str, Any]:
    """`{"session": s}` inside a transaction, `{}` outside, for driver calls."""
    session = _owned_session()
    return {} if session is None else {"session": session}


@contextmanager
def bound_session(session: Any) -> Iterator[None]:
    token = _current.set((session, asyncio.current_task()))
    try:
        yield
    finally:
        _current.reset(token)
