"""Optional provider-side guards, additive to the local checks (PRD §8.1: Bedrock Guardrails are an extra
layer, not a dependency).

An `ExternalGuard` runs only after the local checks passed and only sees text that is already
PII-redacted. It can only add a block, never lift one. A guard that errors fails closed.

`BedrockGuardrails` calls the `ApplyGuardrail` API through an injected `bedrock-runtime` client, so
this library needs no AWS dependency and tests use a stub. The guardrail id and version are
configuration (`BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION`), never code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

Source = Literal["INPUT", "OUTPUT"]
_INTERVENED = "GUARDRAIL_INTERVENED"
_MAX_REASON = 500


@dataclass(frozen=True, slots=True)
class ExternalVerdict:
    intervened: bool
    reason: str | None = None
    """Policy names only (e.g. ``contentPolicy:PROMPT_ATTACK``), never the checked text."""


@runtime_checkable
class ExternalGuard(Protocol):
    name: str

    async def check(self, texts: tuple[str, ...], *, source: Source) -> ExternalVerdict: ...


class BedrockRuntimeClient(Protocol):
    def apply_guardrail(self, **kwargs: Any) -> dict[str, Any]: ...


class BedrockGuardrails:
    name = "bedrock_guardrails"

    def __init__(self, client: BedrockRuntimeClient, *, guardrail_id: str, guardrail_version: str) -> None:
        if not guardrail_id.strip() or not guardrail_version.strip():
            raise ValueError("Bedrock Guardrails need a guardrail id and version from configuration")
        self._client = client
        self._id = guardrail_id
        self._version = guardrail_version

    async def check(self, texts: tuple[str, ...], *, source: Source) -> ExternalVerdict:
        if not texts:
            return ExternalVerdict(intervened=False)
        response = await asyncio.to_thread(
            self._client.apply_guardrail,
            guardrailIdentifier=self._id,
            guardrailVersion=self._version,
            source=source,
            content=[{"text": {"text": text}} for text in texts],
        )
        if response.get("action") != _INTERVENED:
            return ExternalVerdict(intervened=False)
        return ExternalVerdict(intervened=True, reason=_policies(response.get("assessments") or []))


def _policies(assessments: list[dict[str, Any]]) -> str:
    """Which policies fired, e.g. ``contentPolicy:PROMPT_ATTACK,topicPolicy``, from the response shape."""
    names: list[str] = []
    for assessment in assessments:
        for policy, detail in assessment.items():
            filters = detail.get("filters") if isinstance(detail, dict) else None
            kinds = [str(f.get("type")) for f in filters or [] if isinstance(f, dict) and f.get("type")]
            if kinds:
                names.extend(f"{policy}:{kind}" for kind in kinds)
            else:
                names.append(str(policy))
    return (",".join(dict.fromkeys(names)) or _INTERVENED.lower())[:_MAX_REASON]
