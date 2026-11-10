"""HMAC-SHA256 blind indexes (PRD §7.10 Lookup / dedupe): search and dedupe without decrypting.

The caller passes the org's index key (per-org key material is PII-2's vault key management; PRD §7.10
gives Aadhaar its own per-org key, so ``aadhaar_hash`` must be computed with that key). The
message is ``<field>\\x1f<canonical value>``, so one value indexes differently per field and the many
layouts of one value (``+91 90000 00001`` and ``9000000001``) share an index.
"""

import hashlib
import hmac
from collections.abc import Callable
from enum import StrEnum
from typing import Final

from onb_crypto import normalize

MIN_INDEX_KEY_BYTES: Final = 32
_FIELD_SEPARATOR: Final = "\x1f"


class BlindIndexField(StrEnum):
    EMAIL = "email"
    MOBILE = "mobile"
    PAN = "pan"
    AADHAAR = "aadhaar"

    @property
    def index_name(self) -> str:
        """The stored attribute name, e.g. ``email_hash``."""
        return f"{self.value}_hash"


_CANONICAL: Final[dict[BlindIndexField, Callable[[str], str]]] = {
    BlindIndexField.EMAIL: normalize.email,
    BlindIndexField.MOBILE: normalize.mobile,
    BlindIndexField.PAN: normalize.pan,
    BlindIndexField.AADHAAR: normalize.aadhaar,
}


def blind_index(org_key: bytes, field: BlindIndexField, value: str) -> str:
    """Hex HMAC-SHA256 of the canonical ``value`` under the org's index key."""
    if len(org_key) < MIN_INDEX_KEY_BYTES:
        raise ValueError(f"blind index key must be at least {MIN_INDEX_KEY_BYTES} bytes")
    message = f"{field.value}{_FIELD_SEPARATOR}{_CANONICAL[field](value)}"
    return hmac.new(org_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
