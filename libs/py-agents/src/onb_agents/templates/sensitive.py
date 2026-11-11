"""Find sensitive data a template would put in front of an LLM (FR-T3, PRD §5.5, §7.10).

A task's `inputs` reach its agent's prompt, and the whole template is shown to the Template Builder,
so a template must never carry a sensitive field reference or a raw value. Findings name the kind of
problem and where it is (input keys by position), never the offending text.

Every check runs on a folded copy of the text: the guardrails skeleton (NFKC, format and combining
characters dropped, Cyrillic/Greek look-alikes mapped to Latin), then camelCase split and lower case,
so a zero-width space inside `mobile`, a full-width `m`, a Cyrillic `a` in `aadhaar`, and `contactMobile`
all read as what they look like.
A name *names a sensitive field* when one of its `_`-separated parts is one (`personal_email`,
`aadhaar_no`, `MobileNo`).

In `inputs` (keys and values, at any depth), a finding is:
- a key that names a sensitive field;
- a value that is a sensitive field name (`aadhaar`), except a `channel` naming a channel
  (`channel: email` picks the email channel; the address is resolved at the sink with a grant);
- in any string: a placeholder (`{{ hire.mobile }}`, `${email}`) or a path (`new_hire.pan`,
  `hire/aadhaar`, `hire["aadhaar"]`) with a segment that names a sensitive field. Prose that merely
  mentions a field ("reply by email.") is not a path.
- a number: it is checked as text, so `9876543210` is a raw mobile.

Anywhere in the template (titles, names, ids, conditions, approvers, enum values and inputs):
- a raw value the PII detectors recognise (Aadhaar, PAN, IFSC, mobile, email, bank account);
- a vault token (`ptk_…`): a template is reused across hires, so a token in it is always wrong;
- a vault collection name (`pii_vault*`, `org_keys`, `pii_access_log`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Final

from onb_guardrails.pii import detect
from onb_guardrails.pii.skeleton import skeleton

from onb_agents.registry.manifest import names_a_forbidden_collection

__all__ = [
    "CHANNEL_NAMES",
    "SENSITIVE_NAMES",
    "input_findings",
    "is_sensitive_name",
    "names_a_sensitive_field",
    "placeholders",
    "raw_findings",
    "reference_findings",
]

# The vault fields (onb_crypto.VaultField), the words people use for them, and `pii` itself.
SENSITIVE_NAMES: Final = frozenset(
    {
        "mobile",
        "mobile_number",
        "mobileno",
        "phone",
        "phone_number",
        "phoneno",
        "email",
        "e_mail",
        "emailid",
        "email_address",
        "aadhaar",
        "aadhar",
        "aadhaarno",
        "aadhaar_number",
        "pan",
        "panno",
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
_PART_PATTERN: Final = re.compile(
    r"(?:^|_)(?:" + "|".join(sorted(map(re.escape, SENSITIVE_NAMES), key=len, reverse=True)) + r")(?:_|$)"
)
_PLACEHOLDER: Final = re.compile(r"\{\{([^{}]{1,128})\}\}|\$\{([^{}]{1,128})\}")
# Runs of path characters; each run is split on its separators. One character class: linear time.
_PATH_RUN: Final = re.compile(r"""[A-Za-z0-9_./\[\]"'-]+""")
_PATH_SEPARATORS: Final = re.compile(r"""[./\[\]"']+""")
_SEGMENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_CAMEL: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN: Final = re.compile(r"ptk_", re.IGNORECASE)


def fold(text: str) -> str:
    """The skeleton of `text`: what it looks like, in plain ASCII where it can be."""
    return skeleton(text).text


def _normal(text: str) -> str:
    """`contactMobile` -> `contact_mobile`, `Bank-Account` -> `bank_account`, `mob​ile` -> `mobile`."""
    split = _CAMEL.sub("_", fold(text).strip())
    return re.sub(r"[-\s.]+", "_", split.lower())


def is_sensitive_name(text: str) -> bool:
    """Whether `text` is, as a whole, a sensitive field name (`Aadhaar`, `bank-account`)."""
    return _normal(text) in SENSITIVE_NAMES


def names_a_sensitive_field(text: str) -> bool:
    """Whether one of a name's `_`-separated parts is a sensitive field name."""
    return _PART_PATTERN.search(_normal(text)) is not None


def placeholders(text: str) -> Iterator[list[str]]:
    """The path segments of each `{{ … }}` / `${ … }` placeholder in `text` (folded)."""
    for match in _PLACEHOLDER.finditer(fold(text)):
        yield _SEGMENT.findall(match.group(1) or match.group(2) or "")


def _sensitive_path(text: str) -> bool:
    for segments in placeholders(text):
        if any(names_a_sensitive_field(segment) for segment in segments):
            return True
    for match in _PATH_RUN.finditer(fold(text)):
        segments = [s for s in _PATH_SEPARATORS.split(match.group()) if s]
        if len(segments) > 1 and any(names_a_sensitive_field(segment) for segment in segments):
            return True
    return False


def raw_findings(text: str) -> list[str]:
    """Raw values, vault tokens and vault collection names in one string."""
    folded = fold(text)
    found = [f"a raw {span.type.value.lower()} value" for span in detect(text)]
    if _TOKEN.search(folded):
        found.append("a vault token")
    if names_a_forbidden_collection(folded):
        found.append("a vault collection name")
    return found


def reference_findings(text: str) -> list[str]:
    """Sensitive placeholders and paths in one string (titles, input values)."""
    return ["a reference to a sensitive field"] if _sensitive_path(text) else []


def _value_findings(key: str | None, value: str) -> list[str]:
    found = raw_findings(value) + reference_findings(value)
    if is_sensitive_name(value) and not (key == "channel" and _normal(value) in CHANNEL_NAMES):
        found.append("a sensitive field name as a value")
    return found


def input_findings(inputs: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    """`(where, finding)` for every sensitive key, value or raw value in a task's inputs.

    `where` names keys by position (`inputs[1][0]`), never by their text."""
    stack: list[tuple[str, str | None, object]] = [("inputs", None, inputs)]
    while stack:
        where, key, value = stack.pop(0)
        if isinstance(value, Mapping):
            for index, (child_key, child) in enumerate(value.items()):
                child_where = f"{where}[{index}]"
                if names_a_sensitive_field(str(child_key)):
                    yield child_where, "a key naming a sensitive field"
                yield from ((child_where, f) for f in raw_findings(str(child_key)))
                stack.append((child_where, str(child_key), child))
        elif isinstance(value, list):
            stack.extend((f"{where}[{index}]", key, item) for index, item in enumerate(value))
        elif isinstance(value, str):
            yield from ((where, finding) for finding in _value_findings(key, value))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            yield from ((where, finding) for finding in raw_findings(str(value)))
