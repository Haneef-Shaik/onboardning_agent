"""New-hire and PII contracts (PRD FR-R1 hire part, FR-P1/P2/P4/P5, §7.10, §10 Hires; HIRE-1).

Sensitive values travel inward only: a request body may carry a raw value (`POST /hires`,
`PUT /hires/{id}/pii/{field}`), and only a reveal answers with one. Every hire record shows its
sensitive fields masked (`pii`), never as values or tokens. A reveal needs a reason and is short-lived
in the UI (`expires_in_seconds`). `GET /pii-access-log` lists reveals and sink resolutions.

The request models check shape and size only; the API validates each value with the PII-3 validators
(Aadhaar Verhoeff, PAN, IFSC, mobile, email, bank account) before anything is masked, indexed or stored.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from onb_contracts.envelope import Envelope
from onb_contracts.inputs import RequestModel

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Hire ids are made by the server (`h_` + 24 hex); seeded ones look like `h_acme_001`.
HIRE_ID_PATTERN = r"^h_[A-Za-z0-9_]{1,64}$"
USER_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
# At least one non-space character: a blank reason is no reason.
REASON_PATTERN = r"\S"
MAX_REASON_LENGTH = 500
MAX_PURPOSE_LENGTH = 500
MAX_VALUE_LENGTH = 254
MAX_HIRE_LIMIT = 200
DEFAULT_HIRE_LIMIT = 50
MAX_ACCESS_LOG_LIMIT = 200
DEFAULT_ACCESS_LOG_LIMIT = 50
# Paging reaches at most this many records deep ((page - 1) * limit).
MAX_HIRE_SKIP = 10_000
# How long the UI shows a revealed value (FR-P2 "expires after a short time").
REVEAL_TTL_SECONDS = 30


class PiiField(StrEnum):
    """The sensitive fields of a hire (PRD §7.10). Document scans are HIRE-2's."""

    MOBILE = "mobile"
    EMAIL = "email"
    AADHAAR = "aadhaar"
    PAN = "pan"
    BANK_ACCOUNT = "bank_account"
    IFSC = "ifsc"
    ACCOUNT_HOLDER_NAME = "account_holder_name"


class AlternativeIdType(StrEnum):
    """The ID a hire may give instead of Aadhaar (PRD §7.10 Aadhaar). Only the type is recorded here;
    the document itself is an encrypted upload (HIRE-2)."""

    PASSPORT = "passport"
    VOTER_ID = "voter_id"
    DRIVING_LICENCE = "driving_licence"


_Value = Field(default=None, min_length=1, max_length=MAX_VALUE_LENGTH)


class HirePiiInput(RequestModel):
    """The sensitive values given at creation. Email is required: hires are deduplicated by it."""

    email: str = Field(min_length=3, max_length=MAX_VALUE_LENGTH)
    mobile: str | None = _Value
    aadhaar: str | None = _Value
    pan: str | None = _Value
    bank_account: str | None = _Value
    ifsc: str | None = _Value
    account_holder_name: str | None = _Value


class AadhaarConsentInput(RequestModel):
    """The hire's consent to store their Aadhaar, and for what (PRD §7.10 Aadhaar, Retention)."""

    purpose: str = Field(min_length=1, max_length=MAX_PURPOSE_LENGTH, pattern=REASON_PATTERN)


class NewHireRequest(RequestModel):
    """`POST /hires`. An Aadhaar needs `aadhaar_consent`; a hire who declines gives `alternative_id`."""

    name: str = Field(min_length=1, max_length=200, pattern=REASON_PATTERN)
    role: str = Field(min_length=1, max_length=100, pattern=REASON_PATTERN)
    location: str = Field(min_length=1, max_length=100, pattern=REASON_PATTERN)
    start_date: date
    manager_id: str = Field(pattern=USER_ID_PATTERN)
    notes: str = Field(default="", max_length=1000)
    pii: HirePiiInput
    aadhaar_consent: AadhaarConsentInput | None = None
    alternative_id: AlternativeIdType | None = None


class PiiUpdateRequest(RequestModel):
    """`PUT /hires/{id}/pii/{field}`: a new value (a new vault version and token, FR-P4)."""

    value: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    aadhaar_consent: AadhaarConsentInput | None = None


class RevealRequest(RequestModel):
    """`POST /hires/{id}/pii/{field}/reveal`: the reason is mandatory (FR-P2)."""

    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)


class HireIdProof(BaseModel):
    model_config = _FROZEN

    aadhaar_consent: bool
    alternative_id: AlternativeIdType | None


class HireRecord(BaseModel):
    """A hire as every role sees it: sensitive fields masked (FR-P1), keyed by field."""

    model_config = _FROZEN

    id: str
    name: str
    role: str
    location: str
    start_date: date
    manager_id: str
    notes: str
    pii: dict[PiiField, str]
    id_proof: HireIdProof
    created_by: str | None
    created_at: datetime | None


class RevealedValue(BaseModel):
    """One revealed value; the UI hides it again after `expires_in_seconds`."""

    model_config = _FROZEN

    hire_id: str
    field: PiiField
    value: str
    access_id: str
    expires_in_seconds: int


class PiiAccessKind(StrEnum):
    REVEAL = "reveal"  # a person, through the API
    RESOLVE = "resolve"  # a sink, under an orchestrator grant (PII-4)


class PiiAccessActorType(StrEnum):
    USER = "user"
    SERVICE = "service"


class PiiAccessActor(BaseModel):
    model_config = _FROZEN

    type: PiiAccessActorType
    id: str


class PiiAccessRecord(BaseModel):
    """One entry of `pii_access_log` (FR-P5): who saw which field of which hire, masked, and why."""

    model_config = _FROZEN

    id: str
    kind: PiiAccessKind
    actor: PiiAccessActor
    hire_id: str
    field: PiiField
    token: str
    masked: str
    reason: str | None
    sink: str | None
    run_id: str | None
    task_id: str | None
    at: datetime


class HireEnvelope(Envelope[HireRecord]):
    """One hire, masked."""


class HiresEnvelope(Envelope[list[HireRecord]]):
    """One page of hires, masked; `meta.page` has the total."""


class RevealEnvelope(Envelope[RevealedValue]):
    """A revealed value. Not cached (`Cache-Control: no-store`)."""


class PiiAccessEnvelope(Envelope[list[PiiAccessRecord]]):
    """One page of the org's PII access log, newest first; `meta.page` has the total."""
