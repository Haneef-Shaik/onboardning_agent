"""What a task agent is asked (AGT-1, ADR 0021).

The agent gets the action, the template's `inputs`, the hire's id, two opaque references and the hire's
non-PII variables. The references exist because the mock integrations (MOCK-1) take 24-hex ids and a run's
task ids are slugs and a hire's id is `h_…`: each reference is a digest of org, kind and ids, so
it is stable across attempts (the mocks' idempotency key). It is not a secret: an unkeyed digest of ids
the agent is already given. Variables are `onb_agents.runs.hire_variables` of the hire record: role,
location, start date and manager id. Nothing else of the hire (name, notes, tokens, masks) is read
into the input.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Final

from onb_agents.runs import hire_variables
from onb_data import DataStore

__all__ = ["agent_input", "opaque_ref"]

_HIRES: Final = "new_hires"
_REF_LENGTH: Final = 24


def opaque_ref(org_id: str, kind: str, *parts: str) -> str:
    """A stable 24-hex reference. Length-prefixed parts, so ("ab", "c") and ("a", "bc") differ."""
    material = "\x00".join(f"{len(p)}:{p}" for p in (org_id, kind, *parts))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_REF_LENGTH]


async def agent_input(
    store: DataStore, org_id: str, run_id: str, template_task: Mapping[str, Any], hire_id: str
) -> dict[str, Any]:
    hire = await store.tenant(_HIRES).find_one({"_id": hire_id})
    return {
        "action": template_task["action"],
        "title": template_task["title"],
        "inputs": dict(template_task.get("inputs") or {}),
        "hire_id": hire_id,
        "task_key": opaque_ref(org_id, "task", run_id, str(template_task["id"])),
        "hire_key": opaque_ref(org_id, "hire", hire_id),
        "variables": hire_variables(hire) if hire else {},
    }
