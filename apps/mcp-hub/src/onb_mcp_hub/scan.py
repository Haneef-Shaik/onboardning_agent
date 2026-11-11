"""The scan of every downstream response before it reaches the agent (PRD §5.2, §7.4, §7.10).

- **Credentials:** a response that contains any credential the hub holds (the call-context key, the
  signed context of this call, a downstream token, its database URI) is blocked whole
  (`credential_in_response`). Agents never hold credentials, so nothing may hand one back.
- **PII:** every string, keys included, goes through the PRD's detectors (`onb_guardrails.pii`);
  raw values become typed markers such as `[MOBILE]`. Tenant documents should only hold `ptk_`
  tokens, so a hit means something upstream leaked: the response is redacted, not passed on.
  Numbers are scanned too. Hex ids under id keys and vault tokens are left as they are (`pii.py`),
  since their digit runs can look like a phone number and the agent needs them intact.
- Only text content is forwarded (`unsupported_content` otherwise). When the result has structured
  content, its text block is rebuilt from the redacted structure.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from mcp.types import CallToolResult, ContentBlock, TextContent

from .pii import count_pii, redact_text, scannable
from .policy import CallDeniedError

__all__ = ["MIN_SECRET_LENGTH", "Scanned", "scan_result"]

MIN_SECRET_LENGTH = 8


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

    def text(self, value: str, key: str | None = None) -> str:
        self.check_secrets(value)
        found = count_pii(value, key)
        if not found:
            return value
        self.redacted += found
        return redact_text(value)

    def value(self, item: Any, key: str | None = None) -> Any:
        if isinstance(item, str):
            return self.text(item, key)
        if isinstance(item, dict):
            return {self.text(str(k)): self.value(child, str(k)) for k, child in item.items()}
        if isinstance(item, list | tuple):
            return [self.value(child, key) for child in item]
        number = scannable(item)
        if number is not None and count_pii(number):
            self.redacted += 1  # a number holding a sensitive value becomes its marker text
            return redact_text(number)
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
        content: list[ContentBlock] = [TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))]
    else:
        structured = None
        content = [TextContent(type="text", text=scanner.text(text)) for text in blocks]
    cleaned = CallToolResult(content=content, structuredContent=structured, isError=result.isError)
    return Scanned(result=cleaned, redacted=scanner.redacted)
