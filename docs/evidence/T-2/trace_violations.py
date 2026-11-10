"""Diagnostic pytest plugin (not committed): attach the call site to each safety-net violation.

For every command the listener would flag, record the innermost frames that live in the repo
(outside .venv), split into product code (src/) and test code (tests_int/). Printed at session end.
"""

from __future__ import annotations

import collections
import traceback

import pytest
from onb_data import safety_net

REPO = "/Users/Adya/personals/onboardning_agent/.claude/worktrees/goal-t-2/"
SITES: collections.Counter[str] = collections.Counter()
_original = safety_net.TenantCommandListener.started


def _site() -> str:
    frames = [f for f in traceback.extract_stack() if f.filename.startswith(REPO) and "/.venv/" not in f.filename]
    frames = [f for f in frames if "safety_net" not in f.filename and "trace_violations" not in f.filename]
    product = [f for f in frames if "/src/" in f.filename]
    tests = [f for f in frames if "tests_int" in f.filename]
    rel = lambda f: f"{f.filename.removeprefix(REPO)}:{f.lineno}"  # noqa: E731
    prod = rel(product[-1]) if product else "-"
    test = rel(tests[-1]) if tests else "-"
    return f"product={prod}  test={test}"


def started(self, event):  # type: ignore[no-untyped-def]
    if self._allowed.get() is None:
        reason = safety_net.find_violation(event.command_name, event.command, event.database_name)
        if reason is not None:
            coll = event.command.get(event.command_name)
            SITES[f"{event.command_name} {coll}: {_site()}"] += 1
    return _original(self, event)


safety_net.TenantCommandListener.started = started  # type: ignore[method-assign]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    print("\n=== violation call sites ===")
    for site, count in sorted(SITES.items()):
        print(f"{count:3d}  {site}")
