"""A stored hire as the API shows it: every sensitive field masked, never a value or a token (FR-P1).

The mask was computed from the validated value when it was written (`pii_masked`), so reading a hire
never decrypts anything. A field whose token has no stored mask (seeded hires) shows `[masked]`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from onb_contracts.hires import AlternativeIdType, HireIdProof, HireRecord, PiiField

from onb_api.hires.fields import UNMASKABLE

__all__ = ["hire_record"]

_FIELDS = {f.value: f for f in PiiField}


def _masked_fields(document: Mapping[str, Any]) -> dict[PiiField, str]:
    tokens = document.get("pii") or {}
    masks = document.get("pii_masked") or {}
    return {_FIELDS[name]: str(masks.get(name, UNMASKABLE)) for name in tokens if name in _FIELDS}


def _id_proof(document: Mapping[str, Any]) -> HireIdProof:
    proof = document.get("id_proof") or {}
    alternative = proof.get("alternative_id")
    return HireIdProof(
        aadhaar_consent=bool(proof.get("aadhaar_consent", False)),
        alternative_id=None if alternative is None else AlternativeIdType(alternative),
    )


def _aware(moment: Any) -> datetime | None:
    if not isinstance(moment, datetime):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def hire_record(document: Mapping[str, Any]) -> HireRecord:
    return HireRecord(
        id=str(document["_id"]),
        name=str(document["name"]),
        role=str(document["role"]),
        location=str(document["location"]),
        start_date=document["start_date"],
        manager_id=str(document["manager_id"]),
        notes=str(document.get("notes", "")),
        pii=_masked_fields(document),
        id_proof=_id_proof(document),
        created_by=document.get("created_by"),
        created_at=_aware(document.get("created_at")),
    )
