"""The shared pytest plugin really fails tests that send unscoped commands (T-2 box 2).

Each case runs a throwaway pytest session in a subprocess, with a conftest that loads
`onb_data.safety_net_pytest` the way the integration suites do. Commands are fed to the listener as
`CommandStartedEvent`s, so no MongoDB is needed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from onb_data import safety_net
from onb_data import safety_net_pytest as plugin
from pymongo import monitoring
from pymongo.monitoring import CommandStartedEvent

CONFTEST = """
from onb_data.safety_net_pytest import (
    pytest_configure, pytest_sessionfinish, tenant_safety_net, tenant_safety_net_guard, unscoped,
)
from pymongo.monitoring import CommandStartedEvent
import pytest

def send(listener, command):
    listener.started(CommandStartedEvent(command, "app", 1, ("127.0.0.1", 27017), 1))

@pytest.fixture(scope="session")
def leaky_session_fixture(tenant_safety_net):
    yield
    send(tenant_safety_net, {"find": "hires", "filter": {}})
"""

UNSCOPED = '{"find": "hires", "filter": {}}'
SCOPED = '{"find": "hires", "filter": {"org_id": "org_alpha"}}'


def run_session(tmp_path: Path, test_body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_case.py").write_text(f"from conftest import send\n\n{test_body}\n")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_a_test_that_sends_an_unscoped_command_fails(tmp_path: Path) -> None:
    result = run_session(tmp_path, f"def test_leak(tenant_safety_net):\n    send(tenant_safety_net, {UNSCOPED})\n")
    assert result.returncode == 1, result.stdout
    assert "UnscopedCommandError" in result.stdout
    assert "1 passed, 1 error" in result.stdout


def test_a_scoped_test_passes(tmp_path: Path) -> None:
    result = run_session(tmp_path, f"def test_ok(tenant_safety_net):\n    send(tenant_safety_net, {SCOPED})\n")
    assert result.returncode == 0, result.stdout


def test_the_guard_is_autouse(tmp_path: Path) -> None:
    body = "def test_plain(request):\n    assert 'tenant_safety_net_guard' in request.fixturenames\n"
    assert run_session(tmp_path, body).returncode == 0


def test_unscoped_with_a_reason_passes(tmp_path: Path) -> None:
    body = (
        "def test_root_read(tenant_safety_net, unscoped):\n"
        f"    with unscoped('root read-back'):\n        send(tenant_safety_net, {UNSCOPED})\n"
    )
    result = run_session(tmp_path, body)
    assert result.returncode == 0, result.stdout


def test_a_violation_after_the_last_test_fails_the_run(tmp_path: Path) -> None:
    result = run_session(tmp_path, "def test_uses_leaky(leaky_session_fixture):\n    pass\n")
    assert result.returncode == 1, result.stdout
    assert "tenant safety net, after the last test" in result.stdout


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str, **_markup: bool) -> None:
        self.lines.append(text)


class _Config:
    def __init__(self) -> None:
        self.writer = _Writer()

    def get_terminal_writer(self) -> _Writer:
        return self.writer


class _Session:
    def __init__(self) -> None:
        self.config = _Config()
        self.exitstatus: int = 0


def test_the_session_hook_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety_net, "_installed", None)
    before = list(monitoring._LISTENERS.command_listeners)
    try:
        plugin.pytest_configure(None)  # type: ignore[arg-type]  # the hook ignores its config
        listener = safety_net.install_safety_net()
        clean = _Session()
        plugin.pytest_sessionfinish(clean, 0)  # type: ignore[arg-type]  # a duck-typed session
        assert clean.exitstatus == 0
        listener.started(CommandStartedEvent({"find": "hires", "filter": {}}, "app", 1, ("127.0.0.1", 27017), 1))
        leaky = _Session()
        plugin.pytest_sessionfinish(leaky, 0)  # type: ignore[arg-type]  # a duck-typed session
        assert leaky.exitstatus == pytest.ExitCode.TESTS_FAILED
        assert "unscoped find on app.hires" in leaky.config.writer.lines[0]
    finally:
        monitoring._LISTENERS.command_listeners[:] = before


def test_the_listener_is_installed_before_any_test(tmp_path: Path) -> None:
    body = (
        "from pymongo import monitoring\n"
        "from onb_data.safety_net import TenantCommandListener\n"
        "def test_installed():\n"
        "    assert any(isinstance(l, TenantCommandListener) for l in monitoring._LISTENERS.command_listeners)\n"
    )
    assert run_session(tmp_path, body).returncode == 0
