"""The MongoDB session of the transaction the current task is running in (EVT-1; PRD §7.2 outbox).

`DataStore.run_transaction` sets it for the duration of its callback. Every repository call reads it
and passes `session=` to the driver, so a domain change and its event commit or roll back together
without callers threading a session through. It is a contextvar: concurrent tasks never share one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

__all__ = ["in_transaction"]

_current: ContextVar[Any | None] = ContextVar("onb_mongo_session", default=None)


def in_transaction() -> bool:
    """True inside a `DataStore.run_transaction` callback."""
    return _current.get() is not None


def session_kwargs() -> dict[str, Any]:
    """`{"session": s}` inside a transaction, `{}` outside, for driver calls."""
    session = _current.get()
    return {} if session is None else {"session": session}


@contextmanager
def bound_session(session: Any) -> Iterator[None]:
    token = _current.set(session)
    try:
        yield
    finally:
        _current.reset(token)
