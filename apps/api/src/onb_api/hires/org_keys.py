"""An org's keys, made on its first sensitive write (ADR 0007): a hire value or a document scan."""

from __future__ import annotations

from onb_crypto import Keyring, KeyVersionConflictError, NoOrgKeysError

__all__ = ["ensure_org_keys"]


async def ensure_org_keys(keyring: Keyring, org_id: str) -> None:
    """Make the org's keys unless it has them; a concurrent maker wins harmlessly."""
    try:
        await keyring.active(org_id)
    except NoOrgKeysError:
        try:
            await keyring.create(org_id)
        except KeyVersionConflictError:
            keyring.forget(org_id)
