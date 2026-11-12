"""Loopback HTTP servers and background workers for the `make test-pii` platform (SEC-1)."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from typing import Any

import uvicorn

START_TIMEOUT_S = 20.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@contextlib.contextmanager
def serve_http(app: Any, name: str, *, access_log: bool = False) -> Iterator[str]:
    """Serve `app` on 127.0.0.1 in a thread; yields its base URL. uvicorn logs go to the root logger,
    so they are part of the scanned application log."""
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=access_log, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name=f"http-{name}", daemon=True)
    thread.start()
    deadline = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"{name} did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


class Background:
    """One event loop in a thread for long-running workers (the orchestrator, the notify consumers).
    `stop()` sets their shared stop event and waits for them to return."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="pii-workers", daemon=True)
        self._thread.start()
        self.stop_event = self.call(self._make_event)
        self._running: list[Any] = []
        self.failures: list[BaseException] = []

    @staticmethod
    async def _make_event() -> asyncio.Event:
        return asyncio.Event()

    def call[T](self, coroutine: Callable[[], Coroutine[Any, Any, T]], timeout: float = 120.0) -> T:
        """Run `coroutine()` on the worker loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coroutine(), self._loop).result(timeout)

    def start(self, coroutine: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        future = asyncio.run_coroutine_threadsafe(coroutine(), self._loop)
        future.add_done_callback(self._record)
        self._running.append(future)

    def _record(self, future: Any) -> None:
        if not future.cancelled() and future.exception() is not None:
            self.failures.append(future.exception())

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self.stop_event.set)
        for future in self._running:
            with contextlib.suppress(Exception):
                future.result(60)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=15)
