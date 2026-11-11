"""The response scan (PRD §7.4 "PII scan of the response", §5.2 "agents never hold credentials")."""

from __future__ import annotations

import json

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent
from onb_mcp_hub.policy import CallDeniedError
from onb_mcp_hub.scan import scan_result

SECRET = "downstream-credential-0123456789abcdef"
MOBILE = "9876543210"


def structured(payload: dict[str, object], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], structuredContent=payload, isError=error
    )


def all_text(result: CallToolResult) -> str:
    texts = [c.text for c in result.content if isinstance(c, TextContent)]
    return json.dumps(result.structuredContent) + "".join(texts)


@pytest.mark.parametrize("mobile", [MOBILE, "+91 98765 43210", "98765-43210", "0 9876543210"])
def test_a_raw_mobile_number_is_redacted_from_structured_and_text_content(mobile: str) -> None:
    payload = {"documents": [{"_id": "6730a9f1c2d3e4f5a6b7c8d9", "note": f"call me on {mobile} today"}], "count": 1}
    scanned = scan_result(structured(payload), secrets=(SECRET,))
    assert scanned.redacted == 1
    assert mobile not in all_text(scanned.result)
    assert "9876543210" not in all_text(scanned.result).replace(" ", "").replace("-", "")
    assert scanned.result.structuredContent == {
        "documents": [{"_id": "6730a9f1c2d3e4f5a6b7c8d9", "note": "call me on [MOBILE] today"}],
        "count": 1,
    }
    assert json.loads(scanned.result.content[0].text) == scanned.result.structuredContent  # type: ignore[union-attr]


def test_a_mobile_in_a_key_or_unstructured_text_is_redacted_too() -> None:
    payload = {"documents": [{MOBILE: "x"}], "count": 1}
    scanned = scan_result(structured(payload), secrets=())
    assert MOBILE not in all_text(scanned.result)
    text_only = CallToolResult(content=[TextContent(type="text", text=f"reach {MOBILE}")])
    scanned = scan_result(text_only, secrets=())
    assert scanned.redacted == 1
    assert scanned.result.content[0].text == "reach [MOBILE]"  # type: ignore[union-attr]


def test_ids_and_vault_tokens_survive_the_scan() -> None:
    ids = {
        "_id": "9876543210aa9876543210aa",  # an ObjectId hex whose digits look like a mobile
        "inserted_id": "6730a9f1c2d3e4f5a6b7c8d9",
        "run_id": "run_9876543210",
        "mobile": "ptk_mobile_9876543210abcdefabcdef",
    }
    scanned = scan_result(structured({"documents": [ids], "count": 1}), secrets=())
    docs = scanned.result.structuredContent["documents"]  # type: ignore[index]
    assert docs[0]["_id"] == ids["_id"]
    assert docs[0]["inserted_id"] == ids["inserted_id"]


def test_a_clean_result_is_returned_unchanged() -> None:
    result = structured({"count": 3})
    scanned = scan_result(result, secrets=(SECRET,))
    assert scanned.redacted == 0
    assert scanned.result.structuredContent == {"count": 3}


@pytest.mark.parametrize(
    "result",
    [
        structured({"documents": [{"note": f"key={SECRET}"}], "count": 1}),
        structured({"documents": [{SECRET: 1}], "count": 1}),
        CallToolResult(content=[TextContent(type="text", text=f"error: bad auth {SECRET}")], isError=True),
    ],
    ids=["value", "key", "error-text"],
)
def test_a_downstream_credential_in_a_response_blocks_it(result: CallToolResult) -> None:
    with pytest.raises(CallDeniedError) as caught:
        scan_result(result, secrets=(SECRET,))
    assert caught.value.reason == "credential_in_response"
    assert SECRET not in str(caught.value)


def test_non_text_content_is_blocked() -> None:
    result = CallToolResult(content=[ImageContent(type="image", data="aGk=", mimeType="image/png")])
    with pytest.raises(CallDeniedError) as caught:
        scan_result(result, secrets=())
    assert caught.value.reason == "unsupported_content"


def test_short_secrets_are_ignored_rather_than_matching_everything() -> None:
    scanned = scan_result(structured({"count": 1}), secrets=("", "1"))
    assert scanned.result.structuredContent == {"count": 1}
