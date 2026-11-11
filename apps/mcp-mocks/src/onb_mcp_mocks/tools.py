"""The mock tool handlers: deterministic results, created at most once per idempotency key.

Ids are derived from a SHA-256 of (org, tool, task id, argument fingerprint), so the same call gets
the same ids in any process, two orgs never share one, and one id never names two different calls.
Usernames use the letters a-p only (hex digits mapped to letters), so no result value can look like a
phone or ID number to a PII detector.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from onb_mcp_mongo.call_context import CallContext
from pydantic import BaseModel

from .contracts import (
    CONTRACTS,
    BitbucketAccount,
    CreateBitbucketAccountArgs,
    LaptopOrder,
    LaptopProfile,
    ProvisionLaptopArgs,
)
from .store import IdempotencyStore, Outcome

__all__ = ["LAPTOP_CATALOG", "invoke", "invoke_outcome"]

# profile → (model, days to deliver). Mock data, no vendor.
LAPTOP_CATALOG: Mapping[LaptopProfile, tuple[str, int]] = {
    "standard": ("Mock Laptop 14, 16 GB", 3),
    "engineering": ("Mock Laptop Pro 16, 32 GB", 5),
    "design": ("Mock Laptop Pro 16, 64 GB", 7),
}
_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _fingerprint(args: BaseModel) -> str:
    return _digest(json.dumps(args.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))


def _bitbucket(ctx: CallContext, args: CreateBitbucketAccountArgs, fingerprint: str) -> dict[str, Any]:
    return BitbucketAccount(
        account_id=_digest(ctx.org_id, "create_bitbucket_account", args.task_id, fingerprint)[:24],
        hire_id=args.hire_id,
        username="nh-" + _digest(ctx.org_id, "username", args.hire_id)[:10].translate(_LETTERS),
        workspace=args.workspace,
        groups=list(args.groups),
        status="active",
    ).model_dump(mode="json")


def _laptop(ctx: CallContext, args: ProvisionLaptopArgs, fingerprint: str) -> dict[str, Any]:
    model, eta_days = LAPTOP_CATALOG[args.profile]
    return LaptopOrder(
        order_id=_digest(ctx.org_id, "provision_laptop", args.task_id, fingerprint)[:24],
        hire_id=args.hire_id,
        profile=args.profile,
        model=model,
        office=args.office,
        eta_days=eta_days,
        status="ordered",
    ).model_dump(mode="json")


_BUILDERS: Mapping[str, Callable[[CallContext, Any, str], dict[str, Any]]] = {
    "create_bitbucket_account": _bitbucket,
    "provision_laptop": _laptop,
}


async def invoke_outcome(store: IdempotencyStore, ctx: CallContext, name: str, args: BaseModel) -> Outcome:
    """Run tool `name` for the context's org; the stored result on a replay."""
    build = _BUILDERS[name]
    contract = CONTRACTS[name]
    if not isinstance(args, contract.args):
        raise TypeError(f"{name} takes {contract.args.__name__}")
    task_id = args.task_id  # type: ignore[attr-defined]
    fingerprint = _fingerprint(args)
    return await store.get_or_create(ctx.org_id, name, task_id, fingerprint, lambda: build(ctx, args, fingerprint))


async def invoke(store: IdempotencyStore, ctx: CallContext, name: str, args: BaseModel) -> BaseModel:
    """The tool's result model for `args`."""
    outcome = await invoke_outcome(store, ctx, name, args)
    return CONTRACTS[name].result.model_validate(outcome.result)
