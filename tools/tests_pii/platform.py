"""The platform `make test-pii` runs (SEC-1): every service built the way its `main` builds it, in this
process, over one scratch database on the compose MongoDB replica set.

- the API (`create_app` with the production services, the production run assistant over the hub);
- the orchestrator worker (`onb_orchestrator.worker.prepare`) running the agent executor on the run bus;
- mcp-mongo, mcp-mocks, mcp-notify (with its `notify.approval` / `notify.in_app` consumers) and the MCP hub,
  each on loopback HTTP;
- telemetry exported over OTLP to the compose collector, so the spans end up in the compose Jaeger.

Only the model is scripted: LLM-1's `fake` connector replays, per task, what a capable model would do
(`scripted_turns`). Every other part (vault, grants, hub policy, sinks, bus, audit) is production code.
Secrets are generated per session and never written to disk.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onb_agents.registry import AgentManifest
from onb_agents.runtime import AgentTask, ModelChoice
from onb_contracts.auth import Role
from onb_crypto import GrantSigningKey, MasterKey, SecretKey
from onb_llm import LLMRuntime, LLMSelection, discover_connectors, load_llm_config

ROOT = Path(__file__).resolve().parents[2]
ORG = "org_piiscan"
HR_ID, ADMIN_ID, MANAGER_ID = "u_pii_hr", "u_pii_hradmin", "u_pii_mgr"
USERS = (
    (HR_ID, "hr", "Scan HR", Role.HR),
    (ADMIN_ID, "hradmin", "Scan Admin", Role.HR_ADMIN),
    (MANAGER_ID, "manager", "Scan Manager", Role.MANAGER),
)
EMAIL_DOMAIN = "piiscan.example.org"
TEMPLATE_KEY = "engineer-india"
WEBHOOK_SECRET_BYTES = 32


def user_email(handle: str) -> str:
    return f"{handle}@{EMAIL_DOMAIN}"


@dataclass(frozen=True)
class Secrets:
    """Per-session keys and passwords: random, held in memory only."""

    password: str = field(default_factory=lambda: secrets.token_urlsafe(18), repr=False)
    jwt: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    token_key: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    mongo_context: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    mocks_context: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    notify_context: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    webhook: str = field(default_factory=lambda: secrets.token_urlsafe(WEBHOOK_SECRET_BYTES), repr=False)
    master: MasterKey = field(default_factory=lambda: MasterKey(SecretKey.generate()), repr=False)
    grant: GrantSigningKey = field(default_factory=GrantSigningKey.generate, repr=False)


class LlmInputs:
    """Every message list any scripted model was sent in this process (the "captured LLM inputs")."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[tuple[str, list[dict[str, Any]]]] = []

    def record(self, model: str, messages: Sequence[Any]) -> None:
        dumped = [m.model_dump(mode="json") if hasattr(m, "model_dump") else {"content": str(m)} for m in messages]
        with self._lock:
            self._calls.append((model, dumped))

    def calls(self) -> list[tuple[str, list[dict[str, Any]]]]:
        with self._lock:
            return list(self._calls)


def install_llm_capture(inputs: LlmInputs) -> Callable[[], None]:
    """Record every call into the fake connector's model (agents and the run assistant alike). Test-only
    instrumentation of the scripted model; returns the undo."""
    from onb_llm.connectors.fake import ScriptedChatModel

    original = ScriptedChatModel._generate

    def recording(self: ScriptedChatModel, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        inputs.record(self.model_name, messages)
        return original(self, messages, *args, **kwargs)

    ScriptedChatModel._generate = recording  # type: ignore[method-assign]

    def undo() -> None:
        ScriptedChatModel._generate = original  # type: ignore[method-assign]

    return undo


# --- the scripted model ----------------------------------------------------------------------------------

Turn = dict[str, Any]


def _final(payload: Mapping[str, Any]) -> Turn:
    return {"content": json.dumps(payload), "usage": {"input_tokens": 50, "output_tokens": 50}}


def _tool(name: str, args: Mapping[str, Any]) -> Turn:
    return {"content": "", "tool_calls": [{"name": name, "args": dict(args)}]}


PROFILE = {"engineer": "engineering", "designer": "design", "sales": "standard"}


def scripted_turns(task: AgentTask, hire: Mapping[str, Any]) -> list[Turn]:
    """What a capable model does for each seed-template action, given the executor's input and what its
    `mongo__find` of the hire returns (`hire`: name and the current mobile token)."""
    given = task.input
    action, variables = given["action"], given["variables"]
    if action == "provision_laptop":
        args = {"task_id": given["task_key"], "hire_id": given["hire_key"], "profile": PROFILE[variables["role"]]}
        args["office"] = str(variables["location"]).lower()
        done = {"status": "ok", "provisioned": [{"action": action, "done": True}]}
        return [_tool("mocks__provision_laptop", args), _final(done)]
    if action == "create_bitbucket_account":
        args = {"task_id": given["task_key"], "hire_id": given["hire_key"], "workspace": "acme-eng", "groups": ["be"]}
        done = {"status": "ok", "provisioned": [{"action": action, "done": True}]}
        return [_tool("mocks__create_bitbucket_account", args), _final(done)]
    if action == "render_and_send":
        body = f"Welcome, {str(hire['name']).split()[0]}! You start on {variables['start_date']}."
        message = {"channel": "telegram", "recipient_token": hire["mobile_token"], "body": body}
        find = {"collection": "new_hires", "filter": {"_id": given["hire_id"]}, "limit": 1}
        return [
            _tool("mongo__find", find),
            _tool("notify__notify_send", message),
            _final({"status": "ok", "message": message}),
        ]
    if action == "approval":
        return [_final({"status": "needs_human", "explanation": "A manager decides; nothing is pre-approved."})]
    find = {"collection": "run_tasks", "filter": {"run_id": task.run_id}, "limit": 20}
    return [_tool("mongo__find", find), _final({"status": "ok", "answer": {"action": action, "done": True}})]


ASSISTANT_SCRIPT: list[Turn] = [
    _tool("mongo__find", {"collection": "run_tasks", "filter": {}, "limit": 20}),
    _final({"status": "ok", "answer": "The run is on track; the welcome message went out.", "citations": []}),
]

HireLookup = Callable[[], Mapping[str, Any]]


def llm_env(script_file: Path) -> dict[str, str]:
    return {
        "LLM_CONNECTOR": "fake",
        "LLM_MODEL_FAST": "fake-fast",
        "LLM_MODEL_STANDARD": "fake-standard",
        "LLM_MODEL_DEEP": "fake-deep",
        "LLM_FAKE_SCRIPT_FILE": str(script_file),
    }


class ScriptedModels:
    """A `ModelSource`: each task's model replays `scripted_turns` on the fake connector. `hire()` gives
    what the model would read back from its `mongo__find` of the hire (name, current mobile token)."""

    def __init__(self, tmp: Path, hire: HireLookup) -> None:
        self._tmp, self._hire = tmp, hire

    def choose(self, manifest: AgentManifest, task: AgentTask) -> ModelChoice:
        path = self._tmp / f"script-{secrets.token_hex(6)}.json"
        path.write_text(json.dumps(scripted_turns(task, self._hire())), encoding="utf-8")
        env = llm_env(path)
        runtime = LLMRuntime.start(load_llm_config(ROOT / "config" / "llm.yaml", env), discover_connectors(), env)
        llm = manifest.llm
        selection = LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())
        resolved, model = runtime.chat_model_for(manifest=selection)
        return ModelChoice(record=resolved.task_record(), model=model)

    def pinned(self, manifest: AgentManifest, record: Mapping[str, Any]) -> ModelChoice:
        raise AssertionError("no task is retried in the scan run")
