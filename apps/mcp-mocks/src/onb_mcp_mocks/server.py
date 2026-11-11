"""The mcp-mocks FastMCP server: the mock tools over streamable HTTP, one span per call.

Each call: verify the hub-signed call context (audience `mcp-mocks`, its own key) from the
`X-Onb-Call-Context` header → the context must name this tool → validate the arguments against the
closed contract → run the tool for the context's org (idempotent on `task_id`) → result. The span
carries `tool.name`, `org_id`, `agent.name`, `run_id`, `mock.replayed` and an `outcome`; never
argument values or results.

Refusals are plain messages (`idempotency_conflict: …`, `invalid arguments: …`); an unexpected
failure is `<tool>: internal error`, the convention the hub uses to tell a failure from a refusal.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from onb_mcp_mongo.call_context import HEADER, CallContext, CallContextError, check_key, verify_call_context
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import ConfigDict, Field, ValidationError

from .contracts import CONTRACTS
from .store import IdempotencyConflictError, IdempotencyStore
from .tools import invoke_outcome

__all__ = ["AUDIENCE", "SERVER_NAME", "SPAN_PREFIX", "build_server"]

SERVER_NAME = "mcp-mocks"
AUDIENCE = "mcp-mocks"
SPAN_PREFIX = "mcp.mocks."
TRACER_NAME = "onb_mcp_mocks"
_log = logging.getLogger("onb_mcp_mocks")


def _header(context: Context[Any, Any, Any] | None) -> str | None:
    try:
        request = context.request_context.request if context is not None else None
    except ValueError:  # outside a request
        return None
    headers = getattr(request, "headers", None)
    return headers.get(HEADER) if headers is not None else None


def _location(err: Any) -> str:
    loc = [str(part) for part in err["loc"]]
    if err["type"] == "extra_forbidden" and loc and loc[-1] != "org_id":
        loc[-1] = "<extra>"  # an agent-invented key is not echoed; org_id is named so the agent learns
    return ".".join(loc) or "<args>"


def _validation_message(exc: ValidationError) -> str:
    """Field paths and error types only: submitted values are never echoed (as in mcp-mongo)."""
    issues = ", ".join(f"{_location(err)} ({err['type']})" for err in exc.errors())
    return f"invalid arguments: {issues}"


def _deny(span: Span, reason: str, message: str) -> ToolError:
    span.set_attributes({"outcome": "denied", "denial.reason": reason})
    return ToolError(message)


class MockTool(Tool):
    """A FastMCP tool that verifies the call context first and records one span per call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    context_key: str = Field(exclude=True, repr=False)
    store: Any = Field(exclude=True, repr=False)

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        with trace.get_tracer(TRACER_NAME).start_as_current_span(
            SPAN_PREFIX + self.name,
            attributes={"tool.name": self.name, "mcp.server": SERVER_NAME},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            ctx = self._context(span, context)
            return await self._run_traced(span, ctx, arguments, convert_result)

    def _context(self, span: Span, context: Any) -> CallContext:
        try:
            ctx = verify_call_context(_header(context), self.context_key, audience=AUDIENCE)
        except CallContextError as exc:
            raise _deny(span, "no_call_context", str(exc)) from None
        span.set_attributes({"org_id": ctx.org_id, "agent.name": ctx.agent, "run_id": ctx.run_id})
        if ctx.tool != self.name:
            raise _deny(span, "context_tool_mismatch", "the call context names another tool")
        return ctx

    async def _run_traced(self, span: Span, ctx: CallContext, arguments: dict[str, Any], convert_result: bool) -> Any:
        contract = CONTRACTS[self.name]
        try:
            args = contract.args.model_validate(arguments)
            outcome = await invoke_outcome(self.store, ctx, self.name, args)
            result = contract.result.model_validate(outcome.result)
            converted = self.fn_metadata.convert_result(result) if convert_result else result
        except ValidationError as exc:
            raise _deny(span, "invalid_arguments", _validation_message(exc)) from None
        except IdempotencyConflictError:
            raise _deny(
                span, "idempotency_conflict", "idempotency_conflict: this task_id was already used with other arguments"
            ) from None
        except Exception as exc:
            span.set_attributes({"outcome": "error", "error.type": type(exc).__name__})
            span.set_status(Status(StatusCode.ERROR))
            _log.error("mcp-mocks %s failed: %s", self.name, type(exc).__name__)
            raise ToolError(f"{self.name}: internal error") from None
        span.set_attributes({"outcome": "ok", "mock.replayed": outcome.replayed})
        return converted


async def _unused(**_: Any) -> None:  # pragma: no cover - FastMCP needs a callable; MockTool.run replaces it
    raise RuntimeError("MockTool.run handles every call")


def _tool(name: str, store: IdempotencyStore, context_key: str) -> MockTool:
    contract = CONTRACTS[name]
    return MockTool(
        fn=_unused,
        name=name,
        title=None,
        description=contract.description,
        parameters=contract.args.model_json_schema(by_alias=True),
        fn_metadata=FuncMetadata(
            arg_model=contract.args,
            output_schema=contract.result.model_json_schema(by_alias=True),
            output_model=contract.result,
        ),
        is_async=True,
        context_kwarg=None,
        annotations=ToolAnnotations(readOnlyHint=contract.read_only, destructiveHint=False, idempotentHint=True),
        context_key=context_key,
        store=store,
    )


def build_server(store: IdempotencyStore, context_key: str, *, host: str = "127.0.0.1", port: int = 8200) -> FastMCP:
    """The server over `store`. `context_key` verifies the hub's call contexts for `mcp-mocks`.

    Stateless JSON responses: every call stands alone and carries its own context. Like mcp-mongo,
    it answers only the hub on the private network, and no call runs without a hub-signed context.
    """
    check_key(context_key)
    tool_list: list[Tool] = [_tool(name, store, context_key) for name in CONTRACTS]
    return FastMCP(
        SERVER_NAME,
        tools=tool_list,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
