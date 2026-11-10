"""The T-2 tenant safety net as pytest hooks and fixtures, shared by every Mongo integration suite.

Test-support only: it imports pytest, so application code never imports it. Each suite's
`tests_int/conftest.py` re-exports these names, which registers them with pytest:

    from onb_data.safety_net_pytest import (
        pytest_configure, pytest_sessionfinish, tenant_safety_net, tenant_safety_net_guard, unscoped,
    )
    __all__ = [...]

`tools/tests/test_safety_net_suites.py` fails `make check` when a suite that talks to Mongo does not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager

import pytest

from .safety_net import TenantCommandListener, install_safety_net

__all__ = [
    "GUARD_FIXTURE",
    "pytest_configure",
    "pytest_sessionfinish",
    "tenant_safety_net",
    "tenant_safety_net_guard",
    "unscoped",
]

GUARD_FIXTURE = "tenant_safety_net_guard"


def pytest_configure(config: pytest.Config) -> None:
    """One global command listener, registered before any client exists, so every client any test
    builds (repository store, root, raw, leaked) reports to it."""
    install_safety_net()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Unscoped commands sent after the last test's guard (session fixture teardown) fail the run."""
    leftover = install_safety_net().drain()
    if leftover:
        lines = "\n".join(v.describe() for v in leftover)
        session.config.get_terminal_writer().line(f"tenant safety net, after the last test:\n{lines}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def tenant_safety_net() -> TenantCommandListener:
    return install_safety_net()


@pytest.fixture(autouse=True)
def tenant_safety_net_guard(tenant_safety_net: TenantCommandListener) -> Iterator[None]:
    """Fails the test (at setup or teardown) if an unscoped command on a tenant collection was sent.

    The worker queue's claim and leased writes pass by shape (ADR 0002). A test that sends an
    unscoped command on purpose drains it (`tenant_safety_net.drain()`/`.check()`); a deliberate root
    read-back across orgs runs inside `unscoped(reason)`.
    """
    with tenant_safety_net.guard():
        yield


@pytest.fixture
def unscoped(tenant_safety_net: TenantCommandListener) -> Callable[[str], AbstractContextManager[None]]:
    """`with unscoped("why"):` lets a test read back as root across orgs to check isolation."""
    return tenant_safety_net.allowing_unscoped
