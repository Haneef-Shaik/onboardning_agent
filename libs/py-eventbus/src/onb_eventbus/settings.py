"""Queue tuning: lease, heartbeat, attempts and backoff (PRD §7.2, §12 Durability).

The defaults suit the MVP. Services read their own values from configuration and pass a
`QueueSettings`; nothing here is a credential.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["QueueSettings", "backoff_delay"]


def _positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")


@dataclass(frozen=True, slots=True)
class QueueSettings:
    lease_seconds: float = 30.0
    """How long a claim holds a job without a heartbeat. After that another worker may take it."""
    heartbeat_seconds: float = 10.0
    """How often a running job's lease is renewed. Must be shorter than the lease."""
    max_attempts: int = 5
    """Claims a job gets, crashed ones included, before it moves to `dead_letters`."""
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 300.0
    poll_seconds: float = 1.0
    """How long an idle worker waits before looking for work again."""

    def __post_init__(self) -> None:
        for name in ("lease_seconds", "heartbeat_seconds", "backoff_base_seconds", "backoff_max_seconds"):
            _positive(name, getattr(self, name))
        _positive("poll_seconds", self.poll_seconds)
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be an integer of at least 1")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds must not exceed backoff_max_seconds")


def backoff_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Seconds to wait after failed attempt number `attempt` (1-based): base, 2x base, 4x base … capped."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt must be a positive integer, got {attempt!r}")
    # Past ~64 doublings any base exceeds any sane cap; avoid building huge integers.
    exponent = min(attempt - 1, 64)
    return float(min(max_seconds, base_seconds * 2**exponent))
