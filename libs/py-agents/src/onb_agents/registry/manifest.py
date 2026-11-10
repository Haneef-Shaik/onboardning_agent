"""`AgentManifest`: one agent's explicit allowlist (PRD §5.2, §7.3).

    name: db_agent
    purpose: Read and update onboarding records for a single run
    llm: { alias: standard }              # or { connector: ..., model: ... }
    mcp:
      mongo:
        tools: [find, aggregate, count, update_one]
        collections:
          read:  [new_hires, run_tasks, documents_meta]
          write: [run_tasks, documents_meta]
    tools: [date_calc]
    memory_scopes: [run_state:read, knowledge:hr_policies]
    requires_approval: [update_one:documents_meta]
    limits: { max_steps: 8, max_tokens: 20000, timeout_s: 60 }

The registry owns `version`: a manifest file does not carry one, and each change synced bumps it.

Rules beyond the shape:
- No string anywhere in a manifest may name `pii_vault*`, `org_keys` or `pii_access_log`. The vault is
  unreachable through MCP (§7.4, §7.10), so no manifest can even ask for it.
- Collections are only granted on the `mongo` server, only for tenant collections an agent may use (no
  global, append-only, queue or `users` collection), and `write` must be a subset of `read`.
  Mongo tools come from the server's tool set (§7.5); a write tool needs write collections, and
  write collections need a write tool.
- `requires_approval` names a granted tool (`create_bitbucket_account`) or, for Mongo writes,
  `tool:collection` where the agent may write that collection (`update_one:documents_meta`).
- `llm` has the shape of `onb_llm.LLMSelection`, and may set only sampling params.

Errors are `ManifestError` with a message naming the field, never a stack of Pydantic internals.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from typing import Annotated, Any, Self

from onb_data import APPEND_ONLY_COLLECTIONS, GLOBAL_COLLECTIONS, QUEUE_COLLECTIONS
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

__all__ = [
    "FORBIDDEN_NAMES",
    "MONGO_READ_TOOLS",
    "MONGO_SERVER",
    "MONGO_TOOLS",
    "MONGO_WRITE_TOOLS",
    "SELECTION_PARAMS",
    "AgentManifest",
    "CollectionGrant",
    "Limits",
    "ManifestError",
    "ManifestLLM",
    "McpGrant",
    "names_a_forbidden_collection",
    "parse_manifest",
]

# Never grantable, matched as a prefix of any identifier in the manifest (`pii_vault_aadhaar` too).
FORBIDDEN_NAMES = ("pii_vault", "org_keys", "pii_access_log")

MONGO_SERVER = "mongo"
MONGO_READ_TOOLS = frozenset({"list_collections", "find", "count", "aggregate"})
MONGO_WRITE_TOOLS = frozenset({"insert_one", "update_one"})
MONGO_TOOLS = MONGO_READ_TOOLS | MONGO_WRITE_TOOLS

# Tenant collections no manifest may list: the logins' user records, and every collection that is not
# an agent's to read (global, append-only and queue collections).
_NOT_GRANTABLE = frozenset({"users"}) | GLOBAL_COLLECTIONS | APPEND_ONLY_COLLECTIONS | QUEUE_COLLECTIONS

# Same rule as onb_llm.resolution.SELECTION_PARAMS (a test keeps them equal): provider wiring comes
# only from llm.yaml.
SELECTION_PARAMS = frozenset({"temperature", "max_tokens", "top_p", "stop"})

_IDENTIFIER = r"^[a-z][a-z0-9_]{0,63}$"
_WORDS = re.compile(r"[a-z0-9_]+")

Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER)]
MemoryScope = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$", max_length=64)]
ApprovalRule = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?$", max_length=128)]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ManifestError(ValueError):
    """A manifest that cannot be registered. The message says which field and why."""


def names_a_forbidden_collection(text: str) -> bool:
    """Whether any identifier in `text` is, or starts with, a forbidden name (case-insensitive)."""
    return any(word.startswith(FORBIDDEN_NAMES) for word in _WORDS.findall(text.lower()))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _strings(item)


def _unique(values: tuple[str, ...], what: str) -> tuple[str, ...]:
    repeated = sorted({v for v in values if values.count(v) > 1})
    if repeated:
        raise ValueError(f"{what} repeat {', '.join(repeated)}")
    return values


class ManifestLLM(BaseModel):
    """`llm:`: an alias from llm.yaml, or an explicit connector and model (both from configuration)."""

    model_config = _FROZEN

    alias: Identifier | None = None
    connector: Identifier | None = None
    model: Annotated[str, StringConstraints(min_length=1, max_length=256, strip_whitespace=True)] | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _alias_xor_connector_and_model(self) -> Self:
        if self.alias is not None:
            if self.connector is not None or self.model is not None:
                raise ValueError("give either `alias` or `connector` and `model`, not both")
        elif self.connector is None or self.model is None:
            raise ValueError("give `alias`, or both `connector` and `model`")
        extra = sorted(set(self.params) - SELECTION_PARAMS)
        if extra:
            raise ValueError(f"params may only set {', '.join(sorted(SELECTION_PARAMS))}, not {', '.join(extra)}")
        return self


class CollectionGrant(BaseModel):
    model_config = _FROZEN

    read: tuple[Identifier, ...] = ()
    write: tuple[Identifier, ...] = ()

    @field_validator("read", "write")
    @classmethod
    def _grantable(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refused = sorted(set(value) & _NOT_GRANTABLE)
        if refused:
            raise ValueError(f"{', '.join(refused)} cannot be granted to an agent")
        return _unique(value, "collections")

    @model_validator(mode="after")
    def _write_within_read(self) -> Self:
        outside = sorted(set(self.write) - set(self.read))
        if outside:
            raise ValueError(f"write collections must also be read collections: {', '.join(outside)}")
        return self


class McpGrant(BaseModel):
    """The tools of one MCP server the agent may call, and (Mongo only) on which collections."""

    model_config = _FROZEN

    tools: tuple[Identifier, ...] = Field(min_length=1)
    collections: CollectionGrant | None = None

    @field_validator("tools")
    @classmethod
    def _unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "tools")


class Limits(BaseModel):
    model_config = _FROZEN

    max_steps: int = Field(ge=1, le=50)
    max_tokens: int = Field(ge=1, le=200_000)
    timeout_s: int = Field(ge=1, le=600)


class AgentManifest(BaseModel):
    model_config = _FROZEN

    name: Identifier
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
    llm: ManifestLLM
    mcp: dict[Identifier, McpGrant] = Field(default_factory=dict)
    tools: tuple[Identifier, ...] = ()
    memory_scopes: tuple[MemoryScope, ...] = ()
    requires_approval: tuple[ApprovalRule, ...] = ()
    limits: Limits

    @model_validator(mode="before")
    @classmethod
    def _never_names_the_vault(cls, data: Any) -> Any:
        named = sorted({s for s in _strings(data) if names_a_forbidden_collection(s)})
        if named:
            raise ValueError(f"a manifest may not name a vault collection ({', '.join(named)})")
        return data

    @field_validator("tools", "memory_scopes", "requires_approval")
    @classmethod
    def _unique_entries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique(value, "entries")

    @model_validator(mode="after")
    def _grants_are_consistent(self) -> Self:
        for server, grant in self.mcp.items():
            if server == MONGO_SERVER:
                _check_mongo(grant)
            elif grant.collections is not None:
                raise ValueError(f"collections are granted on the {MONGO_SERVER} server only, not {server}")
        for rule in self.requires_approval:
            self._check_approval(rule)
        return self

    def _check_approval(self, rule: str) -> None:
        tool, _, collection = rule.partition(":")
        if not collection:
            if not any(tool in grant.tools for grant in self.mcp.values()):
                raise ValueError(f"requires_approval names {tool}, which is not a granted MCP tool")
            return
        mongo = self.mcp.get(MONGO_SERVER)
        if mongo is None or tool not in mongo.tools or tool not in MONGO_WRITE_TOOLS:
            raise ValueError(f"requires_approval names {tool}, which is not a granted Mongo write tool")
        if mongo.collections is None or collection not in mongo.collections.write:
            raise ValueError(f"requires_approval names {collection}, which is not a write collection")

    def document(self) -> dict[str, Any]:
        """The manifest as plain JSON data, as stored in the registry."""
        return self.model_dump(mode="json")

    def sha256(self) -> str:
        """A content hash independent of key order, so an unchanged file never bumps the version."""
        canonical = json.dumps(self.document(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _check_mongo(grant: McpGrant) -> None:
    unknown = sorted(set(grant.tools) - MONGO_TOOLS)
    if unknown:
        raise ValueError(f"{', '.join(unknown)} is not a {MONGO_SERVER} tool")
    collections = grant.collections or CollectionGrant()
    if set(grant.tools) & (MONGO_TOOLS - {"list_collections"}) and not collections.read:
        raise ValueError(f"{MONGO_SERVER} tools need read collections")
    writes = set(grant.tools) & MONGO_WRITE_TOOLS
    if writes and not collections.write:
        raise ValueError(f"{', '.join(sorted(writes))} needs write collections")
    if collections.write and not writes:
        raise ValueError("write collections need a write tool (insert_one or update_one)")


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors(include_url=False, include_input=False):
        where = ".".join(str(p) for p in item["loc"]) or "manifest"
        message = item["msg"].removeprefix("Value error, ")
        parts.append(f"{where}: {message}")
    return "; ".join(parts)


def parse_manifest(data: object) -> AgentManifest:
    """Validate raw manifest data (a YAML mapping) or raise `ManifestError`."""
    if not isinstance(data, Mapping):
        raise ManifestError("a manifest must be a mapping")
    try:
        return AgentManifest.model_validate(dict(data))
    except ValidationError as error:
        raise ManifestError(_describe(error)) from None
