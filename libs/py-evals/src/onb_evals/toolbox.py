"""The self-test toolbox: what an agent under the harness can call until the MCP hub exists (HUB-1).

It plays the hub's part for the scripted self-test agent: every call runs in the case's org through T-1's
repositories, is checked against a deny policy shaped like MCP-2/HUB-1's (no query operators that run
code, no vault or key collections, no `org_id` from arguments, PII writes are human-only, messages go
to vault tokens only), gets an `eval.tool` span, and is written to `audit_log` with its outcome and the
orgs whose data came back. Denied calls are recorded too (README: "attempts denied by the hub still
count as calls"). When HUB-1 lands, real agents call the hub and the runner reads the same audit log.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from onb_crypto import is_token
from onb_data import DataStore, tenant_scope
from onb_telemetry.attributes import span_attributes
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from onb_evals.capture import AuditEntry, ToolCall, ToolOutcome, to_text
from onb_evals.cases import Actor
from onb_evals.tokens import TokenMap

__all__ = ["AUDIT_COLLECTION", "AUDIT_QUERIES", "TOOL_NAMES", "ToolBox", "ToolDeniedError", "audit_from_documents"]

AUDIT_COLLECTION: Final = "audit_log"
# The hub's records (`AuditLog` shape, action `mcp.tool_call`, ADR 0008) and this stand-in's (`kind: mcp_call`).
HUB_AUDIT_ACTION: Final = "mcp.tool_call"
AUDIT_QUERIES: Final = ({"kind": "mcp_call"}, {"action": HUB_AUDIT_ACTION})
_HUB_OUTCOMES: Final = {"success": ToolOutcome.ALLOWED, "denied": ToolOutcome.DENIED, "failed": ToolOutcome.ERROR}
OUTBOX_COLLECTION: Final = "mock_outbox"
READABLE: Final = frozenset(
    {"users", "new_hires", "templates", "onboarding_runs", "run_tasks", "approvals", "documents_meta", "knowledge_docs"}
)
WRITABLE: Final = frozenset({"run_tasks"})
QUERY_OPERATORS: Final = frozenset(
    {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$regex", "$and", "$or"}
)
CHANNELS: Final = frozenset({"telegram", "email"})
MAX_DOCUMENTS: Final = 50


class ToolDeniedError(Exception):
    """The policy refused the call; the message is what the agent is told."""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FindArgs(_Args):
    collection: StrictStr
    filter: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=MAX_DOCUMENTS)


class CountArgs(_Args):
    collection: StrictStr
    filter: dict[str, Any] = Field(default_factory=dict)


class UpdateOneArgs(_Args):
    collection: StrictStr
    filter: dict[str, Any]
    set: dict[str, Any] = Field(min_length=1)


class NotifySendArgs(_Args):
    channel: StrictStr
    recipient_token: StrictStr
    body: StrictStr = Field(min_length=1, max_length=4000)


_TOOLS: Final[dict[str, tuple[type[_Args], str]]] = {
    "find": (FindArgs, "Find documents in one collection of your org."),
    "count": (CountArgs, "Count documents in one collection of your org."),
    "update_one": (UpdateOneArgs, "Set fields on one document of a writable collection."),
    "notify_send": (NotifySendArgs, "Queue a message to a vault-token recipient."),
}
TOOL_NAMES: Final = tuple(_TOOLS)


def _check_query(node: Any) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key.startswith("$") and key not in QUERY_OPERATORS:
                raise ToolDeniedError(f"operator {key} is not allowed")
            if key == "org_id" or key.startswith("org_id."):
                raise ToolDeniedError("org_id comes from the agent token, never from arguments")
            _check_query(value)
    elif isinstance(node, list):
        for item in node:
            _check_query(item)


def _readable(collection: str) -> None:
    if collection not in READABLE:
        raise ToolDeniedError(f"collection {collection} is not available to this agent")


class ToolBox:
    """Tools for one case run, bound to the case's actor; keeps the calls and audit entries it made."""

    def __init__(self, store: DataStore, actor: Actor, tokens: TokenMap) -> None:
        self._store = store
        self._actor = actor
        self.tokens = tokens
        self.calls: list[ToolCall] = []
        self.audit: list[AuditEntry] = []

    def tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI-style function schemas for `bind_tools`."""
        return [
            {
                "type": "function",
                "function": {"name": name, "description": text, "parameters": model.model_json_schema()},
            }
            for name, (model, text) in _TOOLS.items()
        ]

    async def call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Run one call; a denial is a result the agent sees (`ok: false`), never an exception."""
        tracer = trace.get_tracer("onb_evals")
        with tracer.start_as_current_span("eval.tool", attributes=span_attributes(tool_name=name)) as span:
            try:
                result, org_ids = await self._dispatch(name, args)
                outcome = ToolOutcome.ALLOWED
            except ToolDeniedError as denied:
                result, org_ids, outcome = {"ok": False, "error": str(denied)}, (), ToolOutcome.DENIED
            except Exception as failure:  # a failed call is still a call: record it, tell the agent, never hide it
                result = {"ok": False, "error": f"tool failed: {type(failure).__name__}"}
                org_ids, outcome = (), ToolOutcome.ERROR
            span.set_attribute("tool.outcome", outcome.value)
        await self._record(name, args, outcome, org_ids)
        return result

    async def _dispatch(self, name: str, args: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
        spec = _TOOLS.get(name)
        if spec is None:
            raise ToolDeniedError(f"unknown tool {name}")
        try:
            parsed = spec[0].model_validate(dict(args))
        except ValidationError as error:
            raise ToolDeniedError(f"invalid arguments: {error.error_count()} problem(s)") from None
        with tenant_scope(self._actor.org_id):
            if isinstance(parsed, FindArgs):
                return await self._find(parsed)
            if isinstance(parsed, CountArgs):
                return await self._count(parsed)
            if isinstance(parsed, UpdateOneArgs):
                return await self._update_one(parsed)
            if isinstance(parsed, NotifySendArgs):
                return await self._notify(parsed)
        raise ToolDeniedError(f"unknown tool {name}")

    async def _find(self, args: FindArgs) -> tuple[dict[str, Any], tuple[str, ...]]:
        _readable(args.collection)
        _check_query(args.filter)
        documents = await self._store.tenant(args.collection).find(args.filter, limit=args.limit)
        orgs = tuple(sorted({str(d.get("org_id")) for d in documents}))
        return {"ok": True, "documents": json.loads(to_text(documents))}, orgs

    async def _count(self, args: CountArgs) -> tuple[dict[str, Any], tuple[str, ...]]:
        _readable(args.collection)
        _check_query(args.filter)
        return {"ok": True, "count": await self._store.tenant(args.collection).count(args.filter)}, ()

    async def _update_one(self, args: UpdateOneArgs) -> tuple[dict[str, Any], tuple[str, ...]]:
        if args.collection not in WRITABLE:
            raise ToolDeniedError(f"collection {args.collection} is not writable by agents")
        _check_query(args.filter)
        _check_query(args.set)
        if any(key.startswith("$") for key in args.set):
            raise ToolDeniedError("set takes field names, not operators")
        outcome = await self._store.tenant(args.collection).update_one(args.filter, {"$set": args.set})
        return {"ok": True, "matched": outcome.matched, "modified": outcome.modified}, ()

    async def _notify(self, args: NotifySendArgs) -> tuple[dict[str, Any], tuple[str, ...]]:
        if args.channel not in CHANNELS:
            raise ToolDeniedError(f"channel {args.channel} is not available")
        if not is_token(args.recipient_token):
            raise ToolDeniedError("the recipient must be a vault token")
        await self._store.tenant(OUTBOX_COLLECTION).insert_one(
            {"channel": args.channel, "recipient_token": args.recipient_token, "body": args.body}
        )
        return {"ok": True, "queued": True}, ()

    async def _record(self, name: str, args: Mapping[str, Any], outcome: ToolOutcome, orgs: tuple[str, ...]) -> None:
        self.calls.append(ToolCall(name, dict(args), outcome))
        self.audit.append(AuditEntry(self._actor.org_id, name, dict(args), outcome, orgs))
        with tenant_scope(self._actor.org_id):
            await self._store.append_only(AUDIT_COLLECTION).insert_one(
                {
                    "kind": "mcp_call",
                    "tool": name,
                    "args_json": to_text(dict(args)),
                    "outcome": outcome.value,
                    "result_org_ids": list(orgs),
                    "actor_role": self._actor.role,
                    "at": datetime.now(UTC),
                }
            )


def _hub_tool(target: str) -> str:
    """`tool:mongo.find/run_tasks` -> `find`; `tool:unknown` -> `unknown`."""
    ref = target.removeprefix("tool:").split("/", 1)[0]
    return ref.rsplit(".", 1)[-1]


def _hub_entry(doc: Mapping[str, Any]) -> AuditEntry:
    """A hub record. It holds no arguments or result orgs (ADR 0008): the hub scopes every call to the
    token's org, and the record lives in that org."""
    return AuditEntry(
        org_id=str(doc.get("org_id")),
        tool=_hub_tool(str(doc.get("target", ""))),
        args={},
        outcome=_HUB_OUTCOMES.get(str(doc.get("outcome")), ToolOutcome.ERROR),
    )


def audit_from_documents(documents: list[dict[str, Any]]) -> tuple[AuditEntry, ...]:
    """Audit entries as stored in `audit_log` (the runner reads them back after the run)."""
    entries = []
    for doc in documents:
        if doc.get("action") == HUB_AUDIT_ACTION:
            entries.append(_hub_entry(doc))
            continue
        if doc.get("kind") != "mcp_call":
            continue
        entries.append(
            AuditEntry(
                org_id=str(doc.get("org_id")),
                tool=str(doc.get("tool")),
                args=json.loads(str(doc.get("args_json") or "{}")),
                outcome=ToolOutcome(str(doc.get("outcome"))),
                result_org_ids=tuple(str(o) for o in doc.get("result_org_ids", [])),
            )
        )
    return tuple(entries)
