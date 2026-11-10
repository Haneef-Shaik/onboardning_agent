"""Scrubbing one named value, shared by spans and logs (PRD §7.10): PII-3 markers in, `ptk_` tokens kept.

The key matters twice:
- PII-3 finds a bank account number only next to an account keyword ("account no …"), because a
  bare 9-18 digit number is too often an order id or a size. In a span attribute or a log field
  the keyword is in the *key* (`bank_account: 50100…`), so a value under an account-like key is
  scrubbed as if the key's words were in front of it.
- A trace or span id under its own key (`trace_id`, `otelSpanID`, …) is kept when it is exactly a
  W3C hex id, so log-trace correlation survives an id that happens to contain a mobile-shaped run.

The trade-off is deliberate: a number shaped like a valid mobile or Aadhaar is scrubbed whatever it
means (a 10-digit row count starting 6-9 becomes "[MOBILE]"). Losing a metric beats leaking a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from typing import cast

from onb_guardrails.pii import redact
from opentelemetry.util.types import AttributeValue

_ACCOUNT_WORDS = frozenset({"account", "accounts", "acct", "acc", "ac", "iban", "accountno", "accno"})
_KEY_WORDS = re.compile(r"[a-z]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ACCOUNT_PREFIX = "account number: "
_TRACE_ID_KEYS = frozenset({"traceid", "spanid", "oteltraceid", "otelspanid", "parentid", "parentspanid"})
_W3C_HEX_ID = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{32})")


@lru_cache(maxsize=4096)
def scrub_key(key: str) -> str:
    """An attribute or field name with PII removed (keys are few, so this is cached)."""
    return redact(key)


@lru_cache(maxsize=4096)
def is_account_key(key: str | None) -> bool:
    """`bank_account`, `employee.acct`, `acc_no`, `accountNumber`: a key naming a bank account."""
    if not key:
        return False
    words = _KEY_WORDS.findall(_CAMEL_BOUNDARY.sub("_", key).lower())
    return any(word in _ACCOUNT_WORDS for word in words)


def scrub_text(text: str, key: str | None = None) -> str:
    """`text` with every PII value replaced by its marker, reading `key` as context (see module doc)."""
    if key is not None and _is_trace_id(key, text):
        return text
    if is_account_key(key):
        scrubbed = redact(_ACCOUNT_PREFIX + text)
        if scrubbed.startswith(_ACCOUNT_PREFIX):
            return scrubbed[len(_ACCOUNT_PREFIX) :]
    return redact(text)


def scrub_attributes(attributes: Mapping[str, AttributeValue] | None) -> dict[str, AttributeValue]:
    """A new OTel attribute dict with PII removed from keys and values."""
    return {scrub_key(key): scrub_attribute(key, value) for key, value in (attributes or {}).items()}


def scrub_attribute(key: str, value: AttributeValue) -> AttributeValue:
    """An OTel attribute value scrubbed. A number is kept unless its digits are PII, when it becomes
    the marker string; if any element of a sequence had to become a string, every element does,
    because OTel attribute sequences hold one type."""
    if isinstance(value, str):
        return scrub_text(value, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return _scrub_number(key, value)
    if value is None or isinstance(value, Mapping):
        return cast(AttributeValue, scrub_object(key, value))
    items = tuple(scrub_attribute(key, item) for item in value)
    if any(type(new) is not type(old) for new, old in zip(items, value, strict=True)):
        return tuple(str(item) for item in items)
    return items


def scrub_object(key: str | None, value: object) -> object:
    """Any Python value scrubbed into a new value: mappings and lists/tuples recursively, strings and
    numbers as above, and any other object (bytes included) by its text, replaced by the scrubbed
    text only if that text held PII. A value without PII comes back as the same object."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return scrub_text(value, key)
    if isinstance(value, int | float):
        return _scrub_number(key, value)
    if isinstance(value, Mapping):
        return {_scrub_any_key(k): scrub_object(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(scrub_object(key, item) for item in value)
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    scrubbed = scrub_text(text, key)
    return value if scrubbed == text else scrubbed


def _scrub_number(key: str | None, value: int | float) -> int | float | str:
    text = str(value)
    scrubbed = scrub_text(text, key)
    return value if scrubbed == text else scrubbed


def _scrub_any_key(key: object) -> object:
    return scrub_key(key) if isinstance(key, str) else key


def _is_trace_id(key: str, text: str) -> bool:
    return key.replace("_", "").replace(".", "").lower() in _TRACE_ID_KEYS and _W3C_HEX_ID.fullmatch(text) is not None
