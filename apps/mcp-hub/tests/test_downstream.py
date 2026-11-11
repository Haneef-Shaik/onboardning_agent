"""McpDownstream: one URL, one key and one audience per downstream server (HUB-1, MOCK-1)."""

from __future__ import annotations

import asyncio

import httpx
import jwt
import pytest
from onb_mcp_hub.catalog import mocks_catalog, mongo_catalog
from onb_mcp_hub.downstream import McpDownstream, UnknownServerError
from onb_mcp_mongo.call_context import HEADER, CallContext

MONGO_KEY = "hub-unit-test-mongo-context-key-0123456789"
MOCKS_KEY = "hub-unit-test-mocks-context-key-0123456789"
CTX = CallContext(
    org_id="org_acme", agent="provisioning_agent", run_id="run_1", tool="x", read=frozenset(), write=frozenset()
)
URLS = {"mongo": "http://mcp-mongo/mcp", "mocks": "http://mcp-mocks/mcp"}


class _Stop(Exception):
    """Raised by the fake transport once the first request shows the header the hub sent."""


def _capture(seen: list[str]) -> object:
    def client(headers: dict[str, str]) -> httpx.AsyncClient:
        seen.append(headers[HEADER])

        def refuse(request: httpx.Request) -> httpx.Response:
            raise _Stop

        return httpx.AsyncClient(transport=httpx.MockTransport(refuse), headers=headers)

    return client


def _signed_for(downstream: McpDownstream, tool_name: str, catalog: dict[str, object], seen: list[str]) -> str:
    tool = catalog[tool_name]
    with pytest.raises(BaseException):  # noqa: B017, PT011 - the fake transport stops the session after the header is seen
        asyncio.run(downstream.call(tool, {}, CTX))  # type: ignore[arg-type]
    return seen[-1]


def test_each_server_gets_a_context_signed_with_its_own_key_for_its_own_audience() -> None:
    seen: list[str] = []
    downstream = McpDownstream(URLS, MONGO_KEY, keys={"mocks": MOCKS_KEY}, client=_capture(seen))  # type: ignore[arg-type]
    to_mocks = _signed_for(downstream, "mocks__provision_laptop", dict(mocks_catalog()), seen)
    to_mongo = _signed_for(downstream, "mongo__find", dict(mongo_catalog()), seen)
    assert jwt.decode(to_mocks, MOCKS_KEY, algorithms=["HS256"], audience="mcp-mocks")["tool"] == "provision_laptop"
    assert jwt.decode(to_mongo, MONGO_KEY, algorithms=["HS256"], audience="mcp-mongo")["tool"] == "find"
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(to_mocks, MONGO_KEY, algorithms=["HS256"], audience="mcp-mocks")


def test_the_downstream_holds_every_key_as_a_secret() -> None:
    downstream = McpDownstream(URLS, MONGO_KEY, keys={"mocks": MOCKS_KEY})
    assert set(downstream.secrets) == {MONGO_KEY, MOCKS_KEY}


def test_a_short_server_key_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        McpDownstream(URLS, MONGO_KEY, keys={"mocks": "short"})


def test_a_server_without_a_url_is_unknown() -> None:
    downstream = McpDownstream({"mongo": URLS["mongo"]}, MONGO_KEY, keys={"mocks": MOCKS_KEY})
    with pytest.raises(UnknownServerError):
        asyncio.run(downstream.call(mocks_catalog()["mocks__provision_laptop"], {}, CTX))
