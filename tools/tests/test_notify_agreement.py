"""One name and one contract for the notification tool everywhere (NOTIF-1; REG-1's open question).

Settled: server `notify`, tool `notify_send`, hub name `notify__notify_send`, audit target
`tool:notify.notify_send`, arguments `onb_contracts.notify.NotifySendArgs`. The REG-1 manifest, the
hub catalog, the notify server, the eval dataset's checks, the eval scripts and the eval harness's
stand-in all have to agree, or this fails. It lives with the repo tooling because no app may
import the eval harness (py-evals' boundary test).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from onb_agents.registry import load_manifests
from onb_contracts.notify import NOTIFY_SEND, NOTIFY_SERVER, NotifySendArgs
from onb_evals.toolbox import ToolBox
from onb_mcp_hub.catalog import hub_name, notify_catalog
from onb_mcp_mocks.notify.server import NotifyArgs

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = {m.name: m for m in load_manifests(ROOT / "config" / "agents")}
DATASET = ROOT / "evals" / "datasets" / "onboarding_agents_v1.jsonl"
SELFTEST = ROOT / "evals" / "selftest"
SCRIPTS = (SELFTEST / "scripted_outputs_v1.jsonl", SELFTEST / "negative_controls_v1.jsonl")


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _notify_calls(value: object) -> list[dict[str, Any]]:
    """Every scripted `{"name": "notify_send", "args": …}` tool call anywhere in a row."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("name") == NOTIFY_SEND and isinstance(value.get("args"), dict):
            found.append(value["args"])
        for item in value.values():
            found.extend(_notify_calls(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_notify_calls(item))
    return found


def test_the_manifest_grants_the_tool_the_hub_serves_and_the_server_lists() -> None:
    assert MANIFESTS["templates_agent"].mcp[NOTIFY_SERVER].tools == (NOTIFY_SEND,)
    assert list(notify_catalog()) == [hub_name(NOTIFY_SERVER, NOTIFY_SEND)] == ["notify__notify_send"]
    assert notify_catalog()["notify__notify_send"].audit_name == "notify.notify_send"


def test_the_hub_the_server_and_the_eval_harness_use_the_one_shared_contract() -> None:
    assert issubclass(NotifyArgs, NotifySendArgs)
    shared = NotifySendArgs.model_json_schema()
    assert set(notify_catalog()["notify__notify_send"].input_schema["properties"]) == set(shared["properties"])
    box = ToolBox.__new__(ToolBox)  # the schemas need no store
    [spec] = [s["function"] for s in box.tool_schemas() if s["function"]["name"] == NOTIFY_SEND]
    assert spec["parameters"] == shared


def test_every_eval_check_on_the_tool_names_it_and_uses_its_arguments() -> None:
    checks = [
        check
        for row in _rows(DATASET)
        for check in row.get("checks", [])
        if check.get("type") in ("tool_called", "tool_not_called") and "notify" in str(check.get("tool"))
    ]
    assert checks, "the dataset checks the notification tool"
    for check in checks:
        assert check["tool"] == NOTIFY_SEND
        assert set(check.get("args_subset", {})) <= set(NotifySendArgs.model_fields)


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_scripted_call_is_valid_under_the_contract(path: Path) -> None:
    calls = [args for row in _rows(path) for args in _notify_calls(row)]
    assert calls, f"{path.name} scripts the notification tool"
    for args in calls:
        NotifySendArgs.model_validate(args)
