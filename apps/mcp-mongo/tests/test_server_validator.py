"""The validator wired into the server (MCP-2): every corpus payload sent by a real MCP client is a
denial with its named reason on the span, and nothing reaches the driver."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.types import CallToolResult
from onb_mcp_mongo.call_context import HEADER, sign_call_context
from onb_mcp_mongo.server import build_server
from onb_telemetry.testing import CapturedTelemetry

from .corpus import CORPUS, W
from .fakes import FakeDatabase, make_store
from .harness import CTX_A, KEY, call, run


def _tool(name: str, args: dict[str, Any]) -> Any:
    async def fn(session: ClientSession) -> CallToolResult:
        return await session.call_tool(name, args)

    return fn


def _denial(telemetry: CapturedTelemetry, tool: str) -> dict[str, Any]:
    spans = [span for span in telemetry.spans() if span.name == f"mcp.mongo.{tool}"]
    assert len(spans) == 1
    return dict(spans[0].attributes or {})


@pytest.mark.parametrize(("pid", "tool", "args", "reason"), CORPUS, ids=[p[0] for p in CORPUS])
def test_every_corpus_payload_is_denied_over_mcp_before_the_driver(
    db: FakeDatabase, telemetry: CapturedTelemetry, pid: str, tool: str, args: dict[str, Any], reason: str
) -> None:
    result = call(db, _tool(tool, args))
    assert result.isError
    assert db.calls() == []
    attrs = _denial(telemetry, tool)
    assert attrs["outcome"] == "denied"
    assert attrs["denial.reason"] == reason


def test_an_update_touching_pii_mobile_is_denied_over_mcp(db: FakeDatabase, telemetry: CapturedTelemetry) -> None:
    db[W].docs.append({"_id": 1, "org_id": "org_a", "state": "todo", "pii": {"mobile": "ptk_m1"}})
    args = {"collection": W, "filter": {"_id": 1}, "update": {"$set": {"pii.mobile": "ptk_m2"}}}
    result = call(db, _tool("update_one", args))
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert result.isError
    assert "pii" in text
    assert "read-only" in text
    assert db.calls() == []
    assert db[W].docs[0]["pii"] == {"mobile": "ptk_m1"}
    assert _denial(telemetry, "update_one")["denial.reason"] == "pii_read_only"


def test_a_set_of_org_id_is_denied_over_mcp(db: FakeDatabase, telemetry: CapturedTelemetry) -> None:
    db[W].docs.append({"_id": 1, "org_id": "org_a", "state": "todo"})
    args = {"collection": W, "filter": {"_id": 1}, "update": {"$set": {"org_id": "org_b"}}}
    result = call(db, _tool("update_one", args))
    assert result.isError
    assert db.calls() == []
    assert db[W].docs[0]["org_id"] == "org_a"
    assert _denial(telemetry, "update_one")["denial.reason"] == "org_id_not_allowed"


@pytest.mark.parametrize("levels", [40, 300])
def test_a_very_deep_filter_sent_as_raw_json_is_a_denial_not_an_internal_error(
    db: FakeDatabase, telemetry: CapturedTelemetry, levels: int
) -> None:
    """Deeper than the SDK client will serialise, so the JSON-RPC body is written by hand."""
    filter_ = '{"x": 1}'
    for _ in range(levels):
        filter_ = '{"$and": [' + filter_ + "]}"
    body = (
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "find", "arguments": '
        '{"collection": "new_hires", "limit": 1, "filter": ' + filter_ + "}}}"
    )
    server = build_server(make_store(db), KEY)
    headers = {
        HEADER: sign_call_context(dataclasses.replace(CTX_A, tool="find"), KEY),
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }

    app = server.streamable_http_app()

    async def post() -> httpx.Response:
        async with server.session_manager.run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://mcp-mongo") as http:
                return await http.post("/mcp", content=body, headers=headers)

    payload = run(post()).json()
    assert db.calls() == []
    assert "internal error" not in json.dumps(payload)
    spans = [s for s in telemetry.spans() if s.name == "mcp.mongo.find"]
    if levels <= 100:
        # The tool runs, and the validator's depth cap denies the call.
        assert payload["result"]["isError"] is True
        (span,) = spans
        assert dict(span.attributes or {})["outcome"] == "denied"
        assert dict(span.attributes or {})["denial.reason"] == "too_deep"
    else:
        # Deeper still, the MCP SDK refuses the request as invalid params before any tool runs.
        assert payload["error"]["code"] == -32602
        assert spans == []


def test_an_allowed_update_still_reaches_the_driver(db: FakeDatabase) -> None:
    db[W].docs.append({"_id": 1, "org_id": "org_a", "state": "todo"})
    args = {"collection": W, "filter": {"_id": 1}, "update": {"$set": {"state": "done"}, "$inc": {"n": 1}}}
    assert not call(db, _tool("update_one", args)).isError
    assert db[W].calls[-1][1]["update"] == {"$set": {"state": "done"}, "$inc": {"n": 1}}
