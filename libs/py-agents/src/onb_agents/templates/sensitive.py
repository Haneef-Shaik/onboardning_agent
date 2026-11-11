"""Find sensitive data a template would put in front of an LLM (FR-T3, PRD §5.5, §7.10).

A task's `inputs` reach its agent's prompt, and the whole template is shown to the Template Builder,
so a template must never carry a sensitive field reference or a raw value. Findings name the kind of
problem and where it is, never the offending text.

In `inputs` (keys and values, at any depth), a finding is:
- a key naming a sensitive field (`mobile`, `recipient_email`, `Bank-Account`, `pii`);
- a value that is exactly a sensitive field name (`aadhaar`), except a `channel` naming a channel
  (`channel: email` picks the email channel; the address is resolved at the sink with a grant);
- in any string: a placeholder (`{{ hire.mobile }}`, `${email}`) or dotted path (`new_hire.pan`)
  with a sensitive segment.

Anywhere in the template (titles, names, conditions, enum values and inputs), a finding is:
- a raw value the PII detectors recognise (Aadhaar, PAN, IFSC, mobile, email, bank account);
- a vault token (`ptk_…`): a template is reused across hires, so a token in it is always wrong;
- a vault collection name (`pii_vault*`, `org_keys`, `pii_access_log`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Final

from onb_guardrails.pii import detect

from onb_agents.registry.manifest import names_a_forbidden_collection

__all__ = [
    "CHANNEL_NAMES",
    "SENSITIVE_NAMES",
    "input_findings",
    "is_sensitive_name",
    "placeholders",
    "raw_findings",
    "reference_findings",
]

# The vault fields (onb_crypto.VaultField), the words people use for them, and `pii` itself.
SENSITIVE_NAMES: Final = frozenset(
    {
        "mobile",
        "mobile_number",
        "phone",
        "phone_number",
        "email",
        "email_address",
        "aadhaar",
        "aadhar",
        "aadhaar_number",
        "pan",
        "pan_number",
        "bank_account",
        "bank_account_number",
        "account_number",
        "ifsc",
        "account_holder_name",
        "pii",
    }
)
CHANNEL_NAMES: Final = frozenset({"telegram", "email"})
_KEY_PATTERN: Final = re.compile(
    r"(?:^|_)(?:" + "|".join(sorted(map(re.escape, SENSITIVE_NAMES), key=len, reverse=True)) + r")(?:_|$)"
)
_PLACEHOLDER: Final = re.compile(r"\{\{\s*([^{}]{1,128}?)\s*\}\}|\$\{\s*([^{}]{1,128}?)\s*\}")
_DOTTED: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)+")
_SEGMENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_TOKEN: Final = re.compile(r"ptk_", re.IGNORECASE)


def _normal(text: str) -> str:
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def is_sensitive_name(text: str) -> bool:
    """Whether `text` is, as a whole, a sensitive field name (`Aadhaar`, `bank-account`)."""
    return _normal(text) in SENSITIVE_NAMES


def _sensitive_key(key: str) -> bool:
    return _KEY_PATTERN.search(_normal(key)) is not None


def placeholders(text: str) -> Iterator[list[str]]:
    """The path segments of each `{{ … }}` / `${ … }` placeholder in `text`."""
    for match in _PLACEHOLDER.finditer(text):
        yield _SEGMENT.findall(match.group(1) or match.group(2) or "")


def _sensitive_path(text: str) -> bool:
    for segments in placeholders(text):
        if any(is_sensitive_name(segment) for segment in segments):
            return True
    return any(
        any(is_sensitive_name(segment) for segment in match.group().split(".")) for match in _DOTTED.finditer(text)
    )


def raw_findings(text: str) -> list[str]:
    """Raw values, vault tokens and vault collection names in one string."""
    found = [f"a raw {span.type.value.lower()} value" for span in detect(text)]
    if _TOKEN.search(text):
        found.append("a vault token")
    if names_a_forbidden_collection(text):
        found.append("a vault collection name")
    return found


def reference_findings(text: str) -> list[str]:
    """Sensitive placeholders and dotted paths in one string (titles, input values)."""
    return ["a reference to a sensitive field"] if _sensitive_path(text) else []


def _value_findings(key: str | None, value: str) -> list[str]:
    found = raw_findings(value) + reference_findings(value)
    if is_sensitive_name(value) and not (key == "channel" and _normal(value) in CHANNEL_NAMES):
        found.append("a sensitive field name as a value")
    return found


def input_findings(inputs: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    """`(where, finding)` for every sensitive key, value or raw value in a task's inputs."""
    stack: list[tuple[str, str | None, object]] = [("inputs", None, inputs)]
    while stack:
        where, key, value = stack.pop(0)
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                child_where = f"{where}.{child_key}"
                if _sensitive_key(str(child_key)):
                    yield child_where, "a key naming a sensitive field"
                yield from ((child_where, f) for f in raw_findings(str(child_key)))
                stack.append((child_where, str(child_key), child))
        elif isinstance(value, list):
            stack.extend((f"{where}[{index}]", key, item) for index, item in enumerate(value))
        elif isinstance(value, str):
            yield from ((where, finding) for finding in _value_findings(key, value))
