"""Fixture facts the checks compare against, read from a seed (`evals/fixtures/seed_v1.json`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onb_evals.context import FixtureFacts

__all__ = ["facts_from_seed"]


def facts_from_seed(seed: Mapping[str, Any]) -> FixtureFacts:
    catalog = seed.get("action_catalog", [])
    templates = {(str(t["org_id"]), str(t["key"]), int(t["version"])): t for t in seed.get("templates", [])}
    agent_tools = {str(agent): frozenset(tools) for agent, tools in seed.get("agent_tools", {}).items()}
    return FixtureFacts(
        pii_values=tuple(str(record["value"]) for record in seed.get("pii_vault_plain", [])),
        action_agents={str(entry["action"]): str(entry["agent"]) for entry in catalog},
        approval_actions=frozenset(str(e["action"]) for e in catalog if e.get("task_type") == "approval"),
        templates=templates,
        agent_tools=agent_tools,
    )
