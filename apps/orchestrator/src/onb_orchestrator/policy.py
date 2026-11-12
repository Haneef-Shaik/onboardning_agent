"""How the orchestrator retries, waits and leases (PRD FR-R4, §12 Durability).

`RunPolicy` is configuration, not data: it comes from the environment (`worker.Settings`) and is the
same for every run of one worker. A task gets `max_attempts` attempts, a crashed worker's takeover
counting as one. After a failed attempt it waits `backoff_base`, then twice that ... capped at
`backoff_max`. A running task holds a lease of `lease_seconds`, renewed every third of it; a lease
nobody renewed is the sign its worker is gone and another worker may take the task over.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["RunPolicy"]

_MAX_DOUBLINGS = 32


def _finite(name: str, value: float, *, minimum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a number of at least {minimum}")


@dataclass(frozen=True, slots=True)
class RunPolicy:
    max_attempts: int = 3
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    lease_seconds: float = 30.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be an integer of at least 1")
        _finite("backoff_base", self.backoff_base, minimum=0)
        _finite("backoff_max", self.backoff_max, minimum=0)
        _finite("lease_seconds", self.lease_seconds, minimum=0.05)
        if self.backoff_base > self.backoff_max:
            raise ValueError("backoff_base must not exceed backoff_max")

    @property
    def heartbeat_seconds(self) -> float:
        return self.lease_seconds / 3

    def backoff(self, attempt: int) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based): base, 2x base, ... capped."""
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        return float(min(self.backoff_max, self.backoff_base * 2 ** min(attempt - 1, _MAX_DOUBLINGS)))
