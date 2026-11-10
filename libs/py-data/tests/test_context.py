"""TenantContext and tenant_scope (PRD §7.9 Request context)."""

from __future__ import annotations

import asyncio

import pytest
from onb_data import NoTenantContextError, TenantContext, current_tenant, tenant_scope


def test_there_is_no_tenant_context_by_default() -> None:
    with pytest.raises(NoTenantContextError):
        current_tenant()


def test_tenant_scope_sets_the_context_and_resets_it_on_exit() -> None:
    with tenant_scope("org_a") as ctx:
        assert ctx == TenantContext("org_a")
        assert current_tenant().org_id == "org_a"
    with pytest.raises(NoTenantContextError):
        current_tenant()


def test_nested_scope_restores_the_outer_tenant() -> None:
    with tenant_scope("org_a"):
        with tenant_scope("org_b"):
            assert current_tenant().org_id == "org_b"
        assert current_tenant().org_id == "org_a"


def test_tenant_scope_resets_even_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError), tenant_scope("org_a"):
        raise RuntimeError("boom")
    with pytest.raises(NoTenantContextError):
        current_tenant()


def test_concurrent_tasks_each_see_only_their_own_tenant() -> None:
    async def worker(org_id: str, started: asyncio.Barrier) -> str:
        with tenant_scope(org_id):
            await started.wait()  # both tasks are inside their scopes at the same time
            return current_tenant().org_id

    async def main() -> list[str]:
        started = asyncio.Barrier(2)
        return list(await asyncio.gather(worker("org_a", started), worker("org_b", started)))

    assert asyncio.run(main()) == ["org_a", "org_b"]


@pytest.mark.parametrize("bad", ["", "   ", "org a", "org$a", "org.a", "x" * 129, None, 42])
def test_an_invalid_org_id_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="org_id"):
        TenantContext(bad)  # type: ignore[arg-type]  # the point is a non-str at runtime
    with pytest.raises(ValueError, match="org_id"), tenant_scope(bad):  # type: ignore[arg-type]
        pass  # pragma: no cover


def test_tenant_context_is_immutable() -> None:
    ctx = TenantContext("org_a")
    with pytest.raises(AttributeError):
        ctx.org_id = "org_b"  # type: ignore[misc]  # frozen dataclass: assignment must fail
