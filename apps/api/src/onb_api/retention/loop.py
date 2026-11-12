"""The retention job's schedule: one pass, wait, repeat until asked to stop (PII-5).

The wait and the stop signal are parameters, so a test drives it with a fake clock and never sleeps. A
pass that raises is logged and the loop goes on: the next pass retries whatever was left.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from onb_api.retention.job import PurgeReport

__all__ = ["Pass", "run_forever"]

log = logging.getLogger(__name__)


class Pass(Protocol):
    async def run_once(self) -> PurgeReport: ...


async def _wait(sleeping: Awaitable[None], stop: asyncio.Event) -> None:
    """Wait out the interval, or return at once when `stop` is set (a stop request must not sit out the sleep)."""
    waiting = asyncio.ensure_future(sleeping)
    stopped = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({waiting, stopped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (waiting, stopped):
            task.cancel()
        await asyncio.gather(waiting, stopped, return_exceptions=True)


async def run_forever(
    job: Pass,
    *,
    interval: float,
    stop: asyncio.Event,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[PurgeReport]:
    """Run `job` every `interval` seconds until `stop` is set; the reports of the passes that completed."""
    reports: list[PurgeReport] = []
    while not stop.is_set():
        try:
            reports.append(await job.run_once())
        except Exception as error:
            log.error("pii retention pass failed: %s", type(error).__name__)
        if stop.is_set():
            break
        await _wait(sleep(interval), stop)
    return reports
