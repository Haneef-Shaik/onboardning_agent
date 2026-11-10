"""Helpers shared by the built-in connectors."""

from __future__ import annotations

import time
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

from onb_llm.connector import HealthStatus

PING_PROMPT = "Reply with the single word OK."


def ping(chat_model: BaseChatModel) -> HealthStatus:
    """One tiny real call. A failure reports only the exception type: provider errors can carry account ids."""
    started = time.perf_counter()
    try:
        reply = chat_model.invoke(PING_PROMPT)
    except Exception as error:
        return HealthStatus(ok=False, detail=type(error).__name__)
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    return HealthStatus(ok=True, detail=reply.text.strip()[:40], latency_ms=elapsed)


def build_and_ping(build: Callable[[], BaseChatModel], check: Callable[[BaseChatModel], HealthStatus]) -> HealthStatus:
    """Build the model, then `check` it; a build failure is an unhealthy status, not an exception."""
    try:
        chat_model = build()
    except Exception as error:
        return HealthStatus(ok=False, detail=type(error).__name__)
    return check(chat_model)
