"""The hub pipeline for one tool call (PRD §7.4):

verify the agent token → load the agent's manifest from the registry → policy (manifest, scopes,
JSON schema, collections, vault hard-deny, approvals) → tenant scope and credentials (a call context
signed for this one call, sent by the downstream client) → call → scan the response (credentials,
PII) → audit → span.

Every call ends in one `mcp.hub.<tool>` span with `outcome` = `ok` | `denied` | `error` and, for a
denial, `denial.reason`. Every call whose org is known (a genuine token, expired or not) ends in one
audit record written through `AuditLog.audit(...)`: actor `agent:<name>`, action `mcp.tool_call`,
target `tool:<server>.<tool>[/<collection>]`, outcome `success` | `denied` | `failed`, reason the
denial code (or `pii_redacted`). A forged token's org claim is unverified, so its denial is a span
and a log line only (ADR 0008).

The audit is fail-closed: an allowed call whose record cannot be written returns no data. Messages
to the agent and span attributes never carry argument values, downstream messages or credentials.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from mcp.types import CallToolResult, TextContent
from onb_agents.registry import ManifestError, RegisteredAgent
from onb_agents.tokens import AgentClaims, AgentTokenError, verify_agent_token
from onb_data import Actor, Auditor, AuditOutcome, tenant_scope
from onb_mcp_mongo.call_context import CallContext
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from .catalog import HubTool
from .policy import CallDeniedError, Grant, authorize
from .scan import scan_result

__all__ = ["AUDIT_ACTION", "SPAN_PREFIX", "AgentLookup", "Downstream", "Gateway"]

SPAN_PREFIX = "mcp.hub."
AUDIT_ACTION = "mcp.tool_call"
TRACER_NAME = "onb_mcp_hub"
_COLLECTION = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_BEARER = re.compile(r"Bearer ([A-Za-z0-9._~+/=-]+)")
_log = logging.getLogger("onb_mcp_hub")


class AgentLookup(Protocol):
    async def get_agent(self, name: str) -> RegisteredAgent | None: ...


class Downstream(Protocol):
    """Forwards a call with the credentials the hub injects; returns the result and those credentials."""

    @property
    def secrets(self) -> tuple[str, ...]: ...

    async def call(
        self, tool: HubTool, args: Mapping[str, Any], context: CallContext
    ) -> tuple[CallToolResult, tuple[str, ...]]: ...


@dataclass
class _Call:
    """What is known about one call so far: enough to audit and trace it at any stage."""

    tool: HubTool | None
    collection: str | None
    claims: AgentClaims | None = None

    @property
    def target(self) -> str:
        if self.tool is None:
            return "tool:unknown"
        suffix = f"/{self.collection}" if self.collection else ""
        return f"tool:{self.tool.audit_name}{suffix}"


def _bearer(header: str | None) -> str | None:
    match = _BEARER.fullmatch(header.strip()) if header else None
    return match.group(1) if match else None


def _error(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


def _collection(tool: HubTool | None, args: Mapping[str, Any]) -> str | None:
    value = args.get("collection") if tool is not None else None
    return value if isinstance(value, str) and _COLLECTION.fullmatch(value) else None


def _result_count(result: CallToolResult) -> int:
    structured = result.structuredContent or {}
    count = structured.get("count")
    return count if isinstance(count, int) else len(structured.get("collections", []) or [])


class Gateway:
    def __init__(
        self,
        *,
        catalog: Mapping[str, HubTool],
        registry: AgentLookup,
        auditor: Auditor,
        downstream: Downstream,
        token_key: str,
        secrets: Sequence[str] = (),
    ) -> None:
        self._catalog = dict(catalog)
        self._registry = registry
        self._auditor = auditor
        self._downstream = downstream
        self._token_key = token_key
        self._secrets = (token_key, *secrets)

    @property
    def catalog(self) -> Mapping[str, HubTool]:
        return self._catalog

    async def call(self, name: str, args: Mapping[str, Any], authorization: str | None) -> CallToolResult:
        """The result the agent sees for `name(args)`, called with the given `Authorization` header."""
        tool = self._catalog.get(name)
        call = _Call(tool=tool, collection=_collection(tool, args))
        attrs: dict[str, str] = {"tool.name": tool.name if tool else "unknown"}
        if tool is not None:
            attrs["mcp.server"] = tool.server
        if call.collection:
            attrs["db.collection"] = call.collection
        span_name = SPAN_PREFIX + (tool.name if tool else "unknown")
        with trace.get_tracer(TRACER_NAME).start_as_current_span(
            span_name, attributes=attrs, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                return await self._run(span, call, args, authorization)
            except CallDeniedError as denied:
                return await self._deny(span, call, denied)

    async def _run(self, span: Span, call: _Call, args: Mapping[str, Any], authorization: str | None) -> CallToolResult:
        claims = call.claims = self._verify(authorization)
        span.set_attributes({"org_id": claims.org_id, "agent.name": claims.agent, "run_id": claims.run_id})
        agent = await self._agent(claims.agent)
        grant = authorize(claims, agent, call.tool, args)
        if call.tool is None:  # authorize has already denied it; this narrows the type
            raise CallDeniedError("unknown_tool", "the hub serves no such tool")
        return await self._forward(span, call, claims, call.tool, args, grant)

    def _verify(self, authorization: str | None) -> AgentClaims:
        try:
            return verify_agent_token(_bearer(authorization), self._token_key).claims
        except AgentTokenError as exc:
            raise CallDeniedError(exc.reason, str(exc), claims=exc.claims) from None

    async def _agent(self, name: str) -> RegisteredAgent | None:
        try:
            return await self._registry.get_agent(name)
        except ManifestError:
            raise CallDeniedError("manifest_invalid", "the agent's registry record does not verify") from None

    async def _forward(
        self, span: Span, call: _Call, claims: AgentClaims, tool: HubTool, args: Mapping[str, Any], grant: Grant
    ) -> CallToolResult:
        context = CallContext(
            org_id=claims.org_id,
            agent=claims.agent,
            run_id=claims.run_id,
            tool=tool.tool,
            read=grant.read,
            write=grant.write,
        )
        try:
            result, sent = await self._downstream.call(tool, args, context)
        except Exception as exc:
            return await self._fail(span, call, exc)
        scanned = scan_result(result, secrets=(*self._secrets, *self._downstream.secrets, *sent))
        if scanned.result.isError:
            raise CallDeniedError("downstream_denied", "the tool server refused the call") from None
        span.set_attributes({"pii.redacted": scanned.redacted, "result.count": _result_count(scanned.result)})
        reason = "pii_redacted" if scanned.redacted else None
        try:
            await self._audit(call, AuditOutcome.SUCCESS, reason)
        except Exception as exc:
            span.set_attributes({"outcome": "error", "error.type": type(exc).__name__, "audit.failed": True})
            span.set_status(Status(StatusCode.ERROR))
            _log.error("mcp-hub audit failed for an allowed call: %s", type(exc).__name__)
            return _error(f"{tool.name}: internal error")
        span.set_attribute("outcome", "ok")
        return scanned.result

    async def _fail(self, span: Span, call: _Call, exc: Exception) -> CallToolResult:
        span.set_attributes({"outcome": "error", "error.type": type(exc).__name__})
        span.set_status(Status(StatusCode.ERROR))
        _log.error("mcp-hub downstream call failed: %s", type(exc).__name__)
        try:
            await self._audit(call, AuditOutcome.FAILED, "downstream_unavailable")
        except Exception as audit_exc:
            span.set_attribute("audit.failed", True)
            _log.error("mcp-hub audit failed: %s", type(audit_exc).__name__)
        name = call.tool.name if call.tool else "unknown"
        return _error(f"{name}: internal error")

    async def _deny(self, span: Span, call: _Call, denied: CallDeniedError) -> CallToolResult:
        span.set_attributes({"outcome": "denied", "denial.reason": denied.reason})
        if call.claims is None:
            call.claims = denied.claims
            if call.claims is not None:
                span.set_attributes({"org_id": call.claims.org_id, "agent.name": call.claims.agent})
        try:
            await self._audit(call, AuditOutcome.DENIED, denied.reason)
        except Exception as exc:
            span.set_attribute("audit.failed", True)
            _log.error("mcp-hub audit failed for a denial: %s", type(exc).__name__)
        return _error(f"denied ({denied.reason}): {denied}")

    async def _audit(self, call: _Call, outcome: AuditOutcome, reason: str | None) -> None:
        if call.claims is None:
            _log.warning("mcp-hub denied a call with no verifiable org; not audited (%s)", call.target)
            return
        with tenant_scope(call.claims.org_id):
            await self._auditor.audit(
                actor=Actor.agent(call.claims.agent),
                action=AUDIT_ACTION,
                target=call.target,
                outcome=outcome,
                reason=reason,
            )
