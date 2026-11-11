"""The scan of every downstream response before it reaches the agent (PRD §5.2, §7.4, §7.10).

- **Credentials:** a response that contains any credential the hub holds (the call-context key, the
  signed context of this call, a downstream token, its database URI) is blocked whole
  (`credential_in_response`). Agents never hold credentials, so nothing may hand one back.
- **PII:** every string, keys included, goes through the PRD's detectors (`onb_guardrails.pii`);
  raw values become typed markers such as `[MOBILE]`. Tenant documents should only hold `ptk_`
  tokens, so a hit means something upstream leaked: the response is redacted, not passed on.
  Whole-string ids (ObjectId hex, 32-hex ids) and vault tokens are left as they are, since their
  digit runs can look like a phone number and the agent needs them intact.
- Only text content is forwarded (`unsupported_content` otherwise). When the result has structured
  content, its text block is rebuilt from the redacted structure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from mcp.types import CallToolResult, TextContent
from onb_crypto.tokens import is_token
from onb_guardrails.pii import detect, redact

from .policy import CallDeniedError

__all__ = ["MIN_SECRET_LENGTH", "Scanned", "scan_result"]

MIN_SECRET_LENGTH = 8
_HEX_ID = re.compile(r"[0-9a-f]{24}|[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class Scanned:
    result: CallToolResult
    redacted: int


class _Scanner:
    def __init__(self, secrets: Collection[str]) -> None:
        self._secrets = tuple(s for s in secrets if isinstance(s, str) and len(s) >= MIN_SECRET_LENGTH)
        self.redacted = 0

    def check_secrets(self, value: str) -> None:
        if any(secret in value for secret in self._secrets):
            raise CallDeniedError(
                "credential_in_response", "the downstream response was withheld: it held a credential"
            )

    def text(self, value: str) -> str:
        self.check_secrets(value)
        if _HEX_ID.fullmatch(value) or is_token(value):
            return value
        found = detect(value)
        if not found:
            return value
        self.redacted += len(found)
        return redact(value)

    def value(self, item: Any) -> Any:
        if isinstance(item, str):
            return self.text(item)
        if isinstance(item, dict):
            return {self.text(str(key)): self.value(child) for key, child in item.items()}
        if isinstance(item, list | tuple):
            return [self.value(child) for child in item]
        return item


def scan_result(result: CallToolResult, *, secrets: Collection[str]) -> Scanned:
    """The result as the agent may see it, or `CallDeniedError` when it must be withheld whole."""
    scanner = _Scanner(secrets)
    if any(not isinstance(block, TextContent) for block in result.content):
        raise CallDeniedError("unsupported_content", "the downstream response held non-text content")
    blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    if result.structuredContent is not None:
        for text in blocks:  # replaced by the structure below; only checked for credentials here
            scanner.check_secrets(text)
        structured = scanner.value(result.structuredContent)
        content = [TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))]
    else:
        structured = None
        content = [TextContent(type="text", text=scanner.text(text)) for text in blocks]
    cleaned = CallToolResult(content=content, structuredContent=structured, isError=result.isError)
    return Scanned(result=cleaned, redacted=scanner.redacted)
