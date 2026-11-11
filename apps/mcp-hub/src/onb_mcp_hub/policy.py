"""The hub's decision for one call (PRD §5.2, §7.4, §8.1 Tool rows): allow with a grant, or `CallDeniedError`.

Order (the first failing check names the denial):

1. the tool is one the hub serves (`unknown_tool`);
2. the token's agent is registered and active (`unknown_agent`, `agent_retired`);
3. no string in the arguments names a vault collection (`vault_denied`): checked before the
   manifest, so no manifest, forged or not, can reach `pii_vault*`, `org_keys`, `pii_access_log`;
4. the manifest grants the server and the tool (`server_not_in_manifest`, `tool_not_in_manifest`);
5. the token's scopes name the tool (`scope_not_granted`);
6. the arguments match the tool's JSON-schema contract (`invalid_arguments`), never name
   `org_id` (`org_id_not_allowed`): tenant scope comes only from the token; and hold no raw PII
   (`raw_pii_in_arguments`): only vault tokens;
7. Mongo: the collection is in the manifest's read (or, for writes, write) list
   (`collection_not_in_manifest`) and an MCP database user holds it (`collection_not_provisioned`);
   mcp-mongo's query validator passes (its own reason, e.g. `forbidden_operator`);
8. a `requires_approval` rule matching the call is approved in the token's scopes
   (`approval_required`, which tells the caller which rule to get approved).

Denial messages name the check and the argument path, never an argument value.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from onb_agents.registry import RegisteredAgent
from onb_agents.registry.manifest import names_a_forbidden_collection
from onb_agents.tokens import AgentClaims
from onb_mcp_mongo import validator as mongo_validator
from onb_mcp_mongo.contracts import CONTRACTS
from onb_mcp_mongo.db_users import DENIED_COLLECTIONS, READ_ONLY, READ_WRITE
from onb_mcp_mongo.policy import ToolRejectedError
from pydantic import ValidationError

from .catalog import MONGO_SERVER, HubTool
from .pii import count_pii, scannable

__all__ = [
    "APPROVAL_SCOPE_PREFIX",
    "FORBIDDEN_COLLECTIONS",
    "MAX_NODES",
    "CallDeniedError",
    "Grant",
    "authorize",
    "recordable_collection",
]

# Hard-denied for every agent, whatever its manifest says (PRD §7.4). Any identifier containing
# `pii_vault`, `org_keys` or `pii_access_log` is refused too (`names_a_forbidden_collection`).
FORBIDDEN_COLLECTIONS = DENIED_COLLECTIONS
PROVISIONED_READ = frozenset(READ_ONLY.collections) | frozenset(READ_WRITE.collections)
PROVISIONED_WRITE = frozenset(READ_WRITE.collections)
APPROVAL_SCOPE_PREFIX = "approved:"
MAX_NODES = 10_000


class CallDeniedError(Exception):
    """A refused call. `reason` is a stable code for the span, the audit record and the caller."""

    def __init__(self, reason: str, message: str, *, claims: AgentClaims | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        # Set only for an expired but genuine agent token: its org is known, so the denial is audited there.
        self.claims = claims


@dataclass(frozen=True, slots=True)
class Grant:
    """What the call context forwards: the agent's usable collections and the call's collection."""

    read: frozenset[str]
    write: frozenset[str]
    collection: str | None


def _walk(value: Any) -> Iterator[tuple[str, Any, str | None]]:
    """Every (kind, item, parent key) in `value`: dict keys as "key", strings as "str", numbers as
    "num". Iterative and capped."""
    stack: list[tuple[Any, str | None]] = [(value, None)]
    seen = 0
    while stack:
        item, parent = stack.pop()
        seen += 1
        if seen > MAX_NODES:
            raise CallDeniedError("too_large", "the arguments are too large")
        if isinstance(item, str):
            yield "str", item, parent
        elif isinstance(item, int | float) and not isinstance(item, bool):
            yield "num", item, parent
        elif isinstance(item, Mapping):
            for key, child in item.items():
                yield "key", key, parent
                stack.append((child, key if isinstance(key, str) else None))
        elif isinstance(item, list | tuple):
            stack.extend((child, parent) for child in item)


def _forbidden(name: str) -> bool:
    return name in FORBIDDEN_COLLECTIONS or names_a_forbidden_collection(name)


def recordable_collection(name: str) -> bool:
    """Whether `name` may be written into an audit target or span: a collection the platform knows
    (one an MCP user holds, or a hard-denied one, so vault attempts stay visible). Never agent text."""
    return name in PROVISIONED_READ or name in FORBIDDEN_COLLECTIONS


def _check_vault(args: Mapping[str, Any]) -> None:
    if any(kind != "num" and isinstance(text, str) and _forbidden(text) for kind, text, _ in _walk(args)):
        raise CallDeniedError("vault_denied", "the vault is unreachable through MCP")


def _check_agent(token: AgentClaims, agent: RegisteredAgent | None) -> RegisteredAgent:
    if agent is None or agent.name != token.agent or agent.manifest.name != token.agent:
        raise CallDeniedError("unknown_agent", "the agent is not registered")
    if not agent.active:
        raise CallDeniedError("agent_retired", "the agent is retired")
    return agent


def _check_manifest(token: AgentClaims, agent: RegisteredAgent, tool: HubTool) -> None:
    grant = agent.manifest.mcp.get(tool.server)
    if grant is None:
        raise CallDeniedError("server_not_in_manifest", f"the agent's manifest grants no {tool.server} tools")
    if tool.tool not in grant.tools:
        raise CallDeniedError("tool_not_in_manifest", f"the agent's manifest does not grant {tool.name}")
    if tool.name not in token.scopes:
        raise CallDeniedError("scope_not_granted", f"this task's token does not grant {tool.name}")


def _check_schema(tool: HubTool, args: Mapping[str, Any]) -> None:
    errors = sorted(Draft202012Validator(dict(tool.input_schema)).iter_errors(dict(args)), key=lambda e: e.json_path)
    if errors:
        issues = ", ".join(f"{e.json_path} ({e.validator})" for e in errors[:5])
        raise CallDeniedError("invalid_arguments", f"invalid arguments: {issues}")
    for kind, text, _ in _walk(args):
        # A key (`{"org_id": …}`, `"x.org_id"`) or a value naming the field (`$rename` target, `"$org_id"`).
        if kind != "num" and isinstance(text, str) and "org_id" in text.lstrip("$").split("."):
            raise CallDeniedError("org_id_not_allowed", "org_id comes from the agent token, never from arguments")


def _check_pii(args: Mapping[str, Any]) -> None:
    """Arguments carry `ptk_` tokens, never raw sensitive values (PRD §5.5, §7.10)."""
    for kind, item, parent in _walk(args):
        text = scannable(item)
        if text is not None and count_pii(text, None if kind == "key" else parent):
            raise CallDeniedError("raw_pii_in_arguments", "arguments may carry vault tokens only, never raw PII")


def _usable(names: tuple[str, ...], provisioned: frozenset[str]) -> frozenset[str]:
    return frozenset(n for n in names if n in provisioned and not _forbidden(n))


def _mongo_grant(agent: RegisteredAgent, tool: HubTool, args: Mapping[str, Any]) -> Grant:
    grant = agent.manifest.mcp[MONGO_SERVER]
    listed = grant.collections
    read_listed, write_listed = (listed.read, listed.write) if listed is not None else ((), ())
    read = _usable(read_listed, PROVISIONED_READ) | _usable(write_listed, PROVISIONED_WRITE)
    write = _usable(write_listed, PROVISIONED_WRITE)
    collection = args.get("collection") if tool.tool != "list_collections" else None
    if collection is not None:
        allowed_listed = (*read_listed, *write_listed) if tool.read_only else write_listed
        provisioned = PROVISIONED_READ if tool.read_only else PROVISIONED_WRITE
        if collection not in allowed_listed:
            raise CallDeniedError(
                "collection_not_in_manifest", f"the agent's manifest does not grant this collection to {tool.name}"
            )
        if collection not in provisioned:
            raise CallDeniedError("collection_not_provisioned", "no MCP database user may use this collection")
    try:
        contract_args = CONTRACTS[tool.tool].args.model_validate(dict(args)).model_dump_one_level()
        mongo_validator.check_call(tool.tool, contract_args)
    except ValidationError:
        raise CallDeniedError("invalid_arguments", "invalid arguments for the tool contract") from None
    except ToolRejectedError as exc:
        raise CallDeniedError(exc.reason, str(exc)) from None
    return Grant(read=read, write=write, collection=collection)


def _check_approval(token: AgentClaims, agent: RegisteredAgent, tool: HubTool, collection: str | None) -> None:
    candidates = {tool.tool} | ({f"{tool.tool}:{collection}"} if collection else set())
    for rule in agent.manifest.requires_approval:
        if rule in candidates and f"{APPROVAL_SCOPE_PREFIX}{rule}" not in token.scopes:
            raise CallDeniedError("approval_required", f"approval required: {rule}")


def authorize(
    token: AgentClaims, agent: RegisteredAgent | None, tool: HubTool | None, args: Mapping[str, Any]
) -> Grant:
    """The grant for this call, or `CallDeniedError`."""
    if tool is None:
        raise CallDeniedError("unknown_tool", "the hub serves no such tool")
    agent = _check_agent(token, agent)
    _check_vault(args)
    _check_manifest(token, agent, tool)
    _check_schema(tool, args)
    _check_pii(args)
    grant = _mongo_grant(agent, tool, args) if tool.server == MONGO_SERVER else Grant(frozenset(), frozenset(), None)
    _check_approval(token, agent, tool, grant.collection)
    return grant
