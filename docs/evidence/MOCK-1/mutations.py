"""MOCK-1 mutation checks: break each guard on purpose, run the named tests, expect a failure, restore.

Run from the repo root: `uv run --frozen python docs/evidence/MOCK-1/mutations.py`. Each mutation is
an exact text replacement in one file; the file is restored from its original bytes afterwards, even
on error. A mutation that leaves its tests green is a SURVIVOR and fails the run.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOCKS = "apps/mcp-mocks/src/onb_mcp_mocks"
HUB = "apps/mcp-hub/src/onb_mcp_hub"


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation(
        "M1 replay creates a new record",
        f"{MOCKS}/store.py",
        "            if record is not None:\n",
        "            if record is not None and False:\n",
        ("apps/mcp-mocks/tests/test_tools.py", "apps/mcp-hub/tests/test_mocks.py"),
    ),
    Mutation(
        "M2 conflict check removed",
        f"{MOCKS}/store.py",
        "                if record.fingerprint != fingerprint:\n",
        "                if False:\n",
        ("apps/mcp-mocks/tests/test_tools.py", "apps/mcp-mocks/tests/test_server.py"),
    ),
    Mutation(
        "M3 org dropped from the record key",
        f"{MOCKS}/store.py",
        "            record = self._records.get((org_id, tool, key))\n",
        "            record = next((r for (o, t, k), r in self._records.items() if (t, k) == (tool, key)), None)\n",
        ("apps/mcp-mocks/tests/test_tools.py", "apps/mcp-hub/tests/test_mocks.py"),
    ),
    Mutation(
        "M4 mocks server ignores the audience (verifies with mcp-mongo's)",
        f"{MOCKS}/server.py",
        "verify_call_context(_header(context), self.context_key, audience=AUDIENCE)",
        "verify_call_context(_header(context), self.context_key, audience='mcp-mongo')",
        ("apps/mcp-mocks/tests/test_server.py",),
    ),
    Mutation(
        "M5 context tool binding removed",
        f"{MOCKS}/server.py",
        "        if ctx.tool != self.name:\n",
        "        if False:\n",
        ("apps/mcp-mocks/tests/test_server.py",),
    ),
    Mutation(
        "M6 verify_call_context ignores its audience argument",
        "apps/mcp-mongo/src/onb_mcp_mongo/call_context.py",
        "            audience=audience,\n",
        "            audience=AUDIENCE,\n",
        ("apps/mcp-mongo/tests/test_call_context.py", "apps/mcp-mocks/tests/test_server.py"),
    ),
    Mutation(
        "M7 hub signs every server with the mongo key",
        f"{HUB}/downstream.py",
        "        key = self._keys.get(tool.server, self._key)\n",
        "        key = self._key\n",
        ("apps/mcp-hub/tests/test_downstream.py", "apps/mcp-hub/tests/test_mocks.py"),
    ),
    Mutation(
        "M8 hub signs every server for the mcp-mongo audience",
        f"{HUB}/downstream.py",
        "audience=audience_for(tool.server))",
        "audience=audience_for('mongo'))",
        ("apps/mcp-hub/tests/test_downstream.py", "apps/mcp-hub/tests/test_mocks.py"),
    ),
    Mutation(
        "M9 manifest tool check skipped (box 3)",
        f"{HUB}/policy.py",
        "    if tool.tool not in grant.tools:\n",
        "    if False:\n",
        ("apps/mcp-hub/tests/test_mocks.py",),
    ),
    Mutation(
        "M10 manifest server check skipped (box 3)",
        f"{HUB}/policy.py",
        "    if grant is None:\n        raise CallDeniedError(\"server_not_in_manifest\"",
        "    if grant is None and False:\n        raise CallDeniedError(\"server_not_in_manifest\"",
        ("apps/mcp-hub/tests/test_mocks.py",),
    ),
    Mutation(
        "M11 hub keys may repeat",
        f"{HUB}/settings.py",
        "    if len({token_key, context_key, mocks_key}) != 3:\n",
        "    if False:\n",
        ("apps/mcp-hub/tests/test_main.py",),
    ),
    Mutation(
        "M12 mocks dropped from the platform catalog",
        f"{HUB}/catalog.py",
        "    return {**mongo_catalog(), **mocks_catalog()}\n",
        "    return {**mongo_catalog()}\n",
        ("apps/mcp-hub/tests/test_mocks.py", "apps/mcp-hub/tests/test_main.py"),
    ),
    Mutation(
        "M13 Telegram stub keeps a copy of the text",
        f"{MOCKS}/telegram.py",
        "        self._sent += 1\n",
        "        self._sent += 1\n        self._last = (chat_id, text)\n",
        ("apps/mcp-mocks/tests/test_telegram.py",),
    ),
    Mutation(
        "M14 Telegram stub skips the Bot API text limit",
        f"{MOCKS}/telegram.py",
        "        if not 1 <= len(text) <= MAX_TEXT_CHARS:\n",
        "        if False:\n",
        ("apps/mcp-mocks/tests/test_telegram.py",),
    ),
    Mutation(
        "M15 mock ids ignore the org",
        f"{MOCKS}/tools.py",
        '        account_id=_digest(ctx.org_id, "create_bitbucket_account", args.task_id)[:24],',
        '        account_id=_digest("create_bitbucket_account", args.task_id)[:24],',
        ("apps/mcp-mocks/tests/test_tools.py",),
    ),
    Mutation(
        "M16 invented argument keys echoed",
        f"{MOCKS}/server.py",
        '        loc[-1] = "<extra>"',
        "        pass",
        ("apps/mcp-mocks/tests/test_server.py",),
    ),
)


def _project(test: str) -> str:
    return test.split("/")[1]


def run(m: Mutation) -> tuple[bool, str]:
    path = ROOT / m.path
    original = path.read_bytes()
    text = original.decode()
    if text.count(m.old) != 1:
        return False, f"BAD MUTATION: anchor found {text.count(m.old)} times"
    try:
        path.write_text(text.replace(m.old, m.new))
        failed_lines: list[str] = []
        for test in m.tests:
            proc = subprocess.run(  # noqa: S603
                ["uv", "run", "--frozen", "pytest", test, "-q", "-p", "no:cacheprovider", "-x", "-rf"],  # noqa: S607
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                failed_lines += [ln for ln in proc.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))][:1]
        return bool(failed_lines), "; ".join(failed_lines) or "all tests passed"
    finally:
        path.write_bytes(original)


def main() -> int:
    survivors = 0
    for m in MUTATIONS:
        killed, detail = run(m)
        survivors += not killed
        print(f"{'KILLED  ' if killed else 'SURVIVED'} {m.name}: {detail}")
    print(f"\n{len(MUTATIONS) - survivors}/{len(MUTATIONS)} mutations killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
