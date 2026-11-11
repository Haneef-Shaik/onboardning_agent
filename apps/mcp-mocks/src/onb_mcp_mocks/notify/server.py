"""The notify FastMCP server (`mcp-notify`): `notify_send` over streamable HTTP, one span per call.

Each call: verify the hub-signed call context (audience `mcp-notify`, its own key) from
`X-Onb-Call-Context` → the context must name `notify_send` → validate the arguments against the
shared contract (`onb_contracts.notify.NotifySendArgs`) → run the sink in the context's org, with the
orchestrator's grant from `X-Onb-Pii-Grant` (forwarded by the hub, never seen by the model).

Refusals are plain messages the hub treats as refusals (`grant_rejected (<reason>): nothing was sent`,
`unknown_recipient: …`, `invalid arguments: …`); an unexpected failure is `notify_send: internal
error`. The span carries `org_id`, `agent.name`, `run_id`, `notify.channel` and an `outcome`, never an
argument value, the grant or a resolved value.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from onb_contracts.notify import NOTIFY_SEND, NotifySendArgs, NotifySendResult
from onb_data import tenant_scope
from onb_mcp_mongo.call_context import HEADER, CallContext, CallContextError, check_key, verify_call_context
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import ConfigDict, Field, ValidationError

from ..server import _validation_message
from .audit import Caller
from .sink import NotifySink, SinkRefusedError

__all__ = ["AUDIENCE", "DESCRIPTION", "GRANT_HEADER", "SERVER_NAME", "SPAN_PREFIX", "NotifyArgs", "build_notify_server"]

SERVER_NAME: Final = "mcp-notify"
AUDIENCE: Final = "mcp-notify"
SPAN_PREFIX: Final = "mcp.notify."
TRACER_NAME: Final = "onb_mcp_mocks.notify"
GRANT_HEADER: Final = "x-onb-pii-grant"  # AG-1's `onb_agents.runtime.task.GRANT_HEADER`, as HTTP sends it
DESCRIPTION: Final = (
    "Send a message to a new hire on Telegram (channel=telegram, recipient_token = the hire's mobile "
    "vault token) or to a platform user in the app (channel=in_app, recipient_user_id). The body is "
    "plain text without vault tokens. Values are resolved only by the sink, with the task's grant."
)
_log = logging.getLogger("onb_mcp_mocks.notify")


class NotifyArgs(NotifySendArgs, ArgModelBase):
    """The shared contract, as a FastMCP argument model."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=False)


def _request_header(context: Context[Any, Any, Any] | None, name: str) -> str | None:
    try:
        request = context.request_context.request if context is not None else None
    except ValueError:  # outside a request
        return None
    headers = getattr(request, "headers", None)
    return headers.get(name) if headers is not None else None


def _deny(span: Span, reason: str, message: str) -> ToolError:
    span.set_attributes({"outcome": "denied", "denial.reason": reason})
    return ToolError(message)


class NotifyTool(Tool):
    """`notify_send`: verifies the call context, then runs the sink in the context's org."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    context_key: str = Field(exclude=True, repr=False)
    sink: Any = Field(exclude=True, repr=False)

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        with trace.get_tracer(TRACER_NAME).start_as_current_span(
            SPAN_PREFIX + self.name,
            attributes={"tool.name": self.name, "mcp.server": SERVER_NAME},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            ctx = self._context(span, context)
            try:
                args = NotifySendArgs.model_validate(arguments)
            except ValidationError as exc:
                raise _deny(span, "invalid_arguments", _validation_message(exc)) from None
            span.set_attribute("notify.channel", args.channel.value)
            result = await self._send(span, ctx, _request_header(context, GRANT_HEADER), args)
            span.set_attributes({"outcome": "ok", "notify.replayed": result.replayed})
            return self.fn_metadata.convert_result(result) if convert_result else result

    def _context(self, span: Span, context: Any) -> CallContext:
        try:
            ctx = verify_call_context(_request_header(context, HEADER), self.context_key, audience=AUDIENCE)
        except CallContextError as exc:
            raise _deny(span, "no_call_context", str(exc)) from None
        span.set_attributes({"org_id": ctx.org_id, "agent.name": ctx.agent, "run_id": ctx.run_id})
        if ctx.tool != self.name:
            raise _deny(span, "context_tool_mismatch", "the call context names another tool")
        return ctx

    async def _send(self, span: Span, ctx: CallContext, grant: str | None, args: NotifySendArgs) -> NotifySendResult:
        caller = Caller(org_id=ctx.org_id, agent=ctx.agent, run_id=ctx.run_id)
        sink: NotifySink = self.sink
        try:
            with tenant_scope(ctx.org_id):
                result = await sink.send(caller, grant, args)
            return NotifySendResult.model_validate(result.model_dump())
        except SinkRefusedError as refused:
            raise _deny(span, refused.reason, str(refused)) from None
        except Exception as exc:  # a server bug or a store failure: never the agent's fault
            span.set_attributes({"outcome": "error", "error.type": type(exc).__name__})
            span.set_status(Status(StatusCode.ERROR))
            _log.error("mcp-notify %s failed: %s", self.name, type(exc).__name__)
            raise ToolError(f"{self.name}: internal error") from None


async def _unused(**_: Any) -> None:  # pragma: no cover - FastMCP needs a callable; NotifyTool.run replaces it
    raise RuntimeError("NotifyTool.run handles every call")


def build_notify_server(sink: NotifySink, context_key: str, *, host: str = "127.0.0.1", port: int = 8210) -> FastMCP:
    """The server over `sink`. `context_key` verifies the hub's call contexts for `mcp-notify`.

    Stateless JSON responses, like mcp-mocks and mcp-mongo: it answers only the hub on the private
    network, and no call runs without a hub-signed context."""
    check_key(context_key)
    tool = NotifyTool(
        fn=_unused,
        name=NOTIFY_SEND,
        title=None,
        description=DESCRIPTION,
        parameters=NotifyArgs.model_json_schema(by_alias=True),
        fn_metadata=FuncMetadata(
            arg_model=NotifyArgs,
            output_schema=NotifySendResult.model_json_schema(by_alias=True),
            output_model=NotifySendResult,
        ),
        is_async=True,
        context_kwarg=None,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
        context_key=context_key,
        sink=sink,
    )
    return FastMCP(
        SERVER_NAME,
        tools=[tool],
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
