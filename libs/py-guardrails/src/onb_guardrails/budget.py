"""Step, token and time budgets for one task, with an optional shared token budget per run (PRD §8.1 Budget).

    run_budget = Budget(BudgetLimits(max_tokens=200_000))                       # per run
    task_budget = Budget(BudgetLimits(max_steps=8, max_tokens=20_000, max_seconds=120), parent=run_budget)

`GuardedChatModel` checks the budget before each model call (no step, token or second left: the call is
not made) and after it (the call pushed tokens or time past a limit: its reply is discarded). Each
charge adds one step and the call's tokens to the task budget and to every parent. A limit of `None`
is no limit. The clock is monotonic and starts when the budget is created.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_steps: int | None = None
    max_tokens: int | None = None
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_tokens"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        seconds = self.max_seconds
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0):
            raise ValueError("max_seconds must be a positive number or None")


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    steps: int = 0
    tokens: int = 0


class Budget:
    def __init__(
        self,
        limits: BudgetLimits,
        *,
        parent: Budget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits
        self._parent = parent
        self._clock = clock
        self._started = clock()
        self._usage = BudgetUsage()

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def usage(self) -> BudgetUsage:
        return self._usage

    def elapsed(self) -> float:
        return self._clock() - self._started

    def remaining_seconds(self) -> float | None:
        """Seconds left on the tightest time limit in the chain, or None when there is none."""
        own = None if self._limits.max_seconds is None else self._limits.max_seconds - self.elapsed()
        inherited = None if self._parent is None else self._parent.remaining_seconds()
        candidates = [value for value in (own, inherited) if value is not None]
        return min(candidates) if candidates else None

    def charge(self, tokens: int) -> None:
        """Record one model call that used `tokens` tokens, here and in every parent."""
        if tokens < 0:
            raise ValueError("tokens must not be negative")
        self._usage = replace(self._usage, steps=self._usage.steps + 1, tokens=self._usage.tokens + tokens)
        if self._parent is not None:
            self._parent.charge(tokens)

    def exceeded(self, *, before_call: bool) -> str | None:
        """Why the budget stops the loop (e.g. ``steps 3/3``), or None.

        Before a call a limit that is used up stops it; after a call only a limit that was passed does.
        A parent's reason is prefixed with ``run``.
        """
        own = self._own_reason(before_call=before_call)
        if own is not None or self._parent is None:
            return own
        inherited = self._parent.exceeded(before_call=before_call)
        return None if inherited is None else f"run {inherited}"

    def _own_reason(self, *, before_call: bool) -> str | None:
        limits, usage = self._limits, self._usage

        def over(used: float, limit: float) -> bool:
            return used >= limit if before_call else used > limit

        if limits.max_steps is not None and over(usage.steps, limits.max_steps):
            return f"steps {usage.steps}/{limits.max_steps}"
        if limits.max_tokens is not None and over(usage.tokens, limits.max_tokens):
            return f"tokens {usage.tokens}/{limits.max_tokens}"
        elapsed = self.elapsed()
        if limits.max_seconds is not None and over(elapsed, limits.max_seconds):
            return f"seconds {elapsed:.1f}/{limits.max_seconds:g}"
        return None
