"""Eval adapters of the agents' own goals, registered on first lookup (see `onb_evals.adapters.adapter_for`)."""

from __future__ import annotations

from onb_evals.adapters import is_registered, register_adapter
from onb_evals.agents.run_assistant import RunAssistantAdapter

__all__ = ["register_builtin_adapters"]


def register_builtin_adapters() -> None:
    """Idempotent: an agent that already has an adapter keeps it."""
    if not is_registered("run_assistant"):
        register_adapter("run_assistant", RunAssistantAdapter)
