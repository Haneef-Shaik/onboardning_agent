"""Operator procedures from docs/runbooks/key-rotation.md, one call per step, each org in its own
`tenant_scope` so the `org_keys` repository scopes every read and write (PRD §7.9, §15)."""

from __future__ import annotations

from collections.abc import Iterable

from onb_data import tenant_scope

from onb_crypto.keyring import Keyring
from onb_crypto.keys import MasterKey

__all__ = ["rewrap_orgs", "rotate_orgs", "verify_orgs"]


async def verify_orgs(keyring: Keyring, org_ids: Iterable[str]) -> dict[str, int]:
    """Steps A1, A4, B4: unwrap every version of every org; the active version per org."""
    result: dict[str, int] = {}
    for org_id in org_ids:
        with tenant_scope(org_id):
            result = {**result, org_id: await keyring.verify(org_id)}
    return result


async def rotate_orgs(keyring: Keyring, org_ids: Iterable[str]) -> dict[str, int]:
    """Step A2: a new key version per org; the new version per org."""
    result: dict[str, int] = {}
    for org_id in org_ids:
        with tenant_scope(org_id):
            result = {**result, org_id: await keyring.rotate(org_id)}
    return result


async def rewrap_orgs(keyring: Keyring, new_master: MasterKey, org_ids: Iterable[str]) -> dict[str, int]:
    """Step B3: re-wrap every org's keys from the keyring's master to `new_master`; versions changed per org."""
    result: dict[str, int] = {}
    for org_id in org_ids:
        with tenant_scope(org_id):
            result = {**result, org_id: await keyring.rewrap(org_id, new_master)}
    return result
