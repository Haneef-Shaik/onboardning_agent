"""The FastMCP server: six tools over streamable HTTP, one span per call (PRD §7.5, §7.7).

Each call: verify the hub-signed call context from the `X-Onb-Call-Context` header → validate the
arguments against the closed contract → policy → `TenantRepository` → result. The span carries
`db.operation`, `db.collection`, `agent.name`, `org_id`, `run_id`, `db.result_count` and an
`outcome`; never documents, filters or error messages.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from onb_data import DataAccessError, DataStore, UnknownCollectionError
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pymongo.errors import OperationFailure

from . import tools
from .call_context import COLLECTION_PATTERN, HEADER, CallContext, CallContextError, check_key, verify_call_context
from .contracts import CONTRACTS
from .policy import ToolRejectedError

__all__ = ["SERVER_NAME", "SPAN_PREFIX", "WRITE_TOOLS", "build_server"]

SERVER_NAME = "mcp-mongo"
SPAN_PREFIX = "mcp.mongo."
_COLLECTION = re.compile(COLLECTION_PATTERN)
TRACER_NAME = "onb_mcp_mongo"
_log = logging.getLogger("onb_mcp_mongo")

# These reach MongoDB as the read-write user; every other tool as the read-only one (MCP-3).
WRITE_TOOLS = frozenset(name for name, contract in CONTRACTS.items() if not contract.read_only)

Handler = Callable[..., Awaitable[BaseModel]]
HANDLERS: dict[str, Handler] = {
    "list_collections": tools.list_collections,
    "find": tools.find,
    "count": tools.count,
    "aggregate": tools.aggregate,
    "insert_one": tools.insert_one,
    "update_one": tools.update_one,
}


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
    """Field paths and error types only: submitted values are never echoed."""
    issues = ", ".join(f"{_location(err)} ({err['type']})" for err in exc.errors())
    return f"invalid arguments: {issues}"


def _result_count(result: BaseModel) -> int:
    count = getattr(result, "count", None)
    return count if isinstance(count, int) else len(getattr(result, "collections", []))


def _deny(span: Span, reason: str, message: str) -> ToolError:
    span.set_attributes({"outcome": "denied", "denial.reason": reason})
    return ToolError(message)


class TracedTool(Tool):
    """A FastMCP tool that verifies the call context first and records one span per call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    context_key: str = Field(exclude=True, repr=False)
    store: Any = Field(exclude=True, repr=False)
    handler: Any = Field(exclude=True, repr=False)

    async def run(self, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        collection = arguments.get("collection") if self.name != "list_collections" else "*"
        attrs: dict[str, str | int] = {"db.system": "mongodb", "db.operation": self.name, "tool.name": self.name}
        if isinstance(collection, str) and (collection == "*" or _COLLECTION.fullmatch(collection)):
            attrs["db.collection"] = collection
        # Looked up per call, so the span always goes to the provider active now.
        with trace.get_tracer(TRACER_NAME).start_as_current_span(
            SPAN_PREFIX + self.name, attributes=attrs, record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("db.result_count", 0)
            return await self._run_traced(span, arguments, context, convert_result)

    def _context(self, span: Span, context: Any) -> CallContext:
        try:
            ctx = verify_call_context(_header(context), self.context_key)
        except CallContextError as exc:
            raise _deny(span, "no_call_context", str(exc)) from None
        span.set_attributes({"org_id": ctx.org_id, "agent.name": ctx.agent, "run_id": ctx.run_id})
        if ctx.tool != self.name:
            raise _deny(span, "context_tool_mismatch", "the call context names another tool")
        return ctx

    async def _run_traced(self, span: Span, arguments: dict[str, Any], context: Any, convert_result: bool) -> Any:
        ctx = self._context(span, context)
        try:
            # Validated here, not through FastMCP, so JSON strings are never parsed into objects.
            args = self.fn_metadata.arg_model.model_validate(arguments)
            result = await self.handler(self.store, ctx, **args.model_dump_one_level())
            converted = self.fn_metadata.convert_result(result) if convert_result else result
        except ValidationError as exc:
            raise _deny(span, "invalid_arguments", _validation_message(exc)) from None
        except ToolRejectedError as exc:
            raise _deny(span, exc.reason, str(exc)) from None
        except UnknownCollectionError:
            raise _deny(span, "collection_not_allowed", "collection is not available through this tool") from None
        except DataAccessError:
            raise _deny(span, "tenant_violation", "the call steps outside the caller's tenant scope") from None
        except (TypeError, ValueError):  # raised by the data layer on a malformed query, update or pipeline
            raise _deny(span, "invalid_query", "the filter, update or pipeline is malformed") from None
        except OperationFailure:
            raise _deny(span, "query_rejected", "MongoDB rejected the query") from None
        except Exception as exc:
            span.set_attributes({"outcome": "error", "error.type": type(exc).__name__})
            span.set_status(Status(StatusCode.ERROR))
            _log.error("mcp-mongo %s failed: %s", self.name, type(exc).__name__)
            raise ToolError(f"{self.name}: internal error") from None
        span.set_attributes({"outcome": "ok", "db.result_count": _result_count(result)})
        return converted


def _tool(name: str, store: DataStore, context_key: str) -> TracedTool:
    contract = CONTRACTS[name]
    return TracedTool(
        fn=HANDLERS[name],
        store=store,
        handler=HANDLERS[name],
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
        annotations=ToolAnnotations(readOnlyHint=contract.read_only, destructiveHint=False, idempotentHint=False),
        context_key=context_key,
    )


def build_server(
    store: DataStore,
    context_key: str,
    *,
    write_store: DataStore | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """The server over `store`. `context_key` verifies the hub's call contexts.

    `store` connects as the read-only MongoDB user and `write_store` as the read-write one; the
    write tools use `write_store` (`main` always passes both). Without it, every tool uses `store`.

    Stateless JSON responses: every call stands alone and carries its own context. DNS-rebinding
    protection is for browsers; this server answers only the hub on the private network, and no
    call succeeds without a context signed by the hub.
    """
    check_key(context_key)
    writes = store if write_store is None else write_store
    tool_list: list[Tool] = [_tool(name, writes if name in WRITE_TOOLS else store, context_key) for name in CONTRACTS]
    return FastMCP(
        SERVER_NAME,
        tools=tool_list,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
