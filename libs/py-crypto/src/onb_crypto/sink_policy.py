"""The field → sink policy (PRD §7.10 Signed grants), loaded from `config/pii_sink_policy.yaml`.

    version: 1
    sinks:
      notify.telegram: [mobile]
      notify.email: [email]

A sink may resolve only the fields listed for it. Aadhaar, PAN and the bank fields are `NEVER_SINKABLE`:
no sink may list them, and both the loader and `SinkPolicy` refuse a policy that tries, so an edit to the
config file cannot open them up. The file is untrusted input; every part is checked and errors never
echo its content beyond the offending key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import yaml

from onb_crypto.errors import CryptoError
from onb_crypto.vault_record import VaultField

__all__ = ["NEVER_SINKABLE", "SINK_ID_PATTERN", "SinkPolicy", "SinkPolicyError", "check_sink_id", "load_sink_policy"]

NEVER_SINKABLE: Final = frozenset(
    {VaultField.AADHAAR, VaultField.PAN, VaultField.BANK_ACCOUNT, VaultField.IFSC, VaultField.ACCOUNT_HOLDER_NAME}
)
# `<service>.<channel>`, e.g. `notify.telegram`: lowercase dotted segments, at least two.
SINK_ID_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}")
_POLICY_VERSION: Final = 1
_TOP_LEVEL_KEYS: Final = frozenset({"version", "sinks"})
_FIELDS_BY_NAME: Final = {field.value: field for field in VaultField}


class SinkPolicyError(CryptoError):
    """The sink policy file or object is malformed, or grants a field no sink may have."""


def check_sink_id(sink_id: object) -> str:
    if not isinstance(sink_id, str) or not SINK_ID_PATTERN.fullmatch(sink_id):
        raise SinkPolicyError("a sink id is lowercase dotted segments, like notify.telegram")
    return sink_id


class SinkPolicy:
    """Which fields each sink may resolve. Immutable."""

    __slots__ = ("_by_sink",)

    _by_sink: Mapping[str, frozenset[VaultField]]

    def __init__(self, by_sink: Mapping[str, frozenset[VaultField]]) -> None:
        checked: dict[str, frozenset[VaultField]] = {}
        for sink_id, fields in by_sink.items():
            check_sink_id(sink_id)
            if not fields or not all(isinstance(f, VaultField) for f in fields):
                raise SinkPolicyError(f"sink {sink_id} needs at least one VaultField")
            forbidden = NEVER_SINKABLE & fields
            if forbidden:
                names = ", ".join(sorted(f.value for f in forbidden))
                raise SinkPolicyError(f"sink {sink_id} lists {names}: these fields are never resolvable at a sink")
            checked[sink_id] = frozenset(fields)
        object.__setattr__(self, "_by_sink", MappingProxyType(checked))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SinkPolicy is immutable")

    @property
    def sinks(self) -> frozenset[str]:
        return frozenset(self._by_sink)

    def fields_for(self, sink_id: str) -> frozenset[VaultField]:
        return self._by_sink.get(sink_id, frozenset())

    def allows(self, field: VaultField, sink_id: str) -> bool:
        """True only for a field the policy lists for that sink, and never for a `NEVER_SINKABLE` field."""
        return field not in NEVER_SINKABLE and field in self.fields_for(sink_id)

    def __repr__(self) -> str:
        listed = {sink: sorted(f.value for f in fields) for sink, fields in sorted(self._by_sink.items())}
        return f"SinkPolicy({listed})"


def load_sink_policy(path: Path) -> SinkPolicy:
    """Read and check the policy file. Raises `SinkPolicyError` for anything unexpected."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise SinkPolicyError(f"cannot read the sink policy at {path}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise SinkPolicyError(f"the sink policy at {path} is not valid YAML") from None
    return SinkPolicy(_parse(data))


def _parse(data: Any) -> dict[str, frozenset[VaultField]]:
    if not isinstance(data, Mapping):
        raise SinkPolicyError("the sink policy must be a mapping with version and sinks")
    unexpected = set(data) - _TOP_LEVEL_KEYS
    if unexpected:
        raise SinkPolicyError(f"unexpected keys in the sink policy: {sorted(map(str, unexpected))}")
    if data.get("version") != _POLICY_VERSION or isinstance(data.get("version"), bool):
        raise SinkPolicyError(f"the sink policy needs version: {_POLICY_VERSION}")
    sinks = data.get("sinks")
    if not isinstance(sinks, Mapping):
        raise SinkPolicyError("the sink policy needs a sinks mapping of sink id to field list")
    return {check_sink_id(sink_id): _fields(sink_id, listed) for sink_id, listed in sinks.items()}


def _fields(sink_id: str, listed: Any) -> frozenset[VaultField]:
    if not isinstance(listed, list):
        raise SinkPolicyError(f"sink {sink_id} needs a list of fields")
    if not listed:
        raise SinkPolicyError(f"sink {sink_id} needs at least one field")
    fields: list[VaultField] = []
    for name in listed:
        field = _FIELDS_BY_NAME.get(name) if isinstance(name, str) else None
        if field is None:
            raise SinkPolicyError(f"sink {sink_id} lists an unknown field")
        if field in fields:
            raise SinkPolicyError(f"sink {sink_id} lists {field.value} twice")
        fields.append(field)
    return frozenset(fields)
