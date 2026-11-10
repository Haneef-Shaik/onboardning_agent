"""MCP-3 mutation checks: break each guard on purpose, run the tests, record which named tests fail,
then restore the file byte for byte. Run from the repo root:

    uv run --frozen python docs/evidence/MCP-3/mutate.py > docs/evidence/MCP-3/mutation-checks.txt
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "apps/mcp-mongo/src/onb_mcp_mongo"
UNIT = "apps/mcp-mongo/tests"
INT = "apps/mcp-mongo/tests_int/test_db_users_int.py"


@dataclass(frozen=True)
class Mutation:
    mid: str
    what: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old, new)
    tests: str = INT


MUTATIONS = [
    Mutation(
        "M1",
        "the read-only role may also insert and update (box 1)",
        (
            (
                "db_users.py",
                'collections=_READABLE, actions=("find",)',
                'collections=_READABLE, actions=("find", "insert", "update")',
            ),
        ),
    ),
    Mutation(
        "M2",
        "the read-only role names pii_vault, org_keys and users (box 2)",
        (
            (
                "db_users.py",
                '    "new_hires",\n    "onboarding_runs",',
                '    "pii_vault",\n    "org_keys",\n    "users",\n    "new_hires",\n    "onboarding_runs",',
            ),
        ),
    ),
    Mutation(
        "M3",
        "the read-write role names pii_vault_aadhaar and pii_access_log (box 2)",
        (
            (
                "db_users.py",
                '_WRITABLE = ("new_hires",',
                '_WRITABLE = ("pii_vault_aadhaar", "pii_access_log", "new_hires",',
            ),
        ),
    ),
    Mutation(
        "M4",
        "the read-write role may also remove (no delete)",
        (
            (
                "db_users.py",
                'actions=("find", "insert", "update")\n)',
                'actions=("find", "insert", "update", "remove")\n)',
            ),
        ),
    ),
    Mutation(
        "M5",
        "the start-up privilege check accepts anything",
        (("db_users.py", "    for privilege in privileges:\n", "    for privilege in []:\n"),),
    ),
    Mutation(
        "M5u",
        "the start-up privilege check accepts anything (unit)",
        (("db_users.py", "    for privilege in privileges:\n", "    for privilege in []:\n"),),
        UNIT,
    ),
    Mutation(
        "M6",
        "write tools use the read-only store",
        (
            (
                "server.py",
                "_tool(name, writes if name in WRITE_TOOLS else store, context_key)",
                "_tool(name, store, context_key)",
            ),
        ),
        UNIT,
    ),
    Mutation(
        "M7",
        "main serves without checking the users",
        (("main.py", "    assert_least_privilege(settings.mongo_uri, READ_ONLY, settings.database)\n", ""),),
        UNIT,
    ),
    Mutation(
        "M8",
        "main hands the read-only store to the write tools",
        (("main.py", "write_store=write_store, host", "write_store=store, host"),),
        UNIT,
    ),
    Mutation(
        "M9",
        "approvals become writable by the read-write user (review fix)",
        (("db_users.py", '_WRITABLE = ("new_hires",', '_WRITABLE = ("approvals", "new_hires",'),),
    ),
    Mutation(
        "M10",
        "the identity check ignores the user's auth database (review fix)",
        (("db_users.py", 'users = [(u.get("user"), u.get("db"))', 'users = [(u.get("user"), ROLE_DATABASE)'),),
        UNIT,
    ),
    Mutation(
        "M11",
        "the start-up check accepts a user missing part of its role (review fix)",
        (("db_users.py", "    if missing:\n", "    if False:\n"),),
        UNIT,
    ),
]

FAILED = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)
SUMMARY = re.compile(r"^=+ (.*(passed|failed).*) =+$", re.MULTILINE)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_one(m: Mutation) -> bool:
    originals = {name: (SRC / name).read_bytes() for name, _, _ in m.edits}
    before = {name: _digest(SRC / name) for name in originals}
    try:
        for name, old, new in m.edits:
            text = (SRC / name).read_text()
            if text.count(old) != 1:
                print(f"{m.mid}: edit target not found once in {name}: {old!r}")
                return False
            (SRC / name).write_text(text.replace(old, new, 1))
        proc = subprocess.run(
            ["uv", "run", "--frozen", "pytest", m.tests, "-q", "-p", "no:cacheprovider", "-rfE", "--no-header"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        for name, data in originals.items():
            (SRC / name).write_bytes(data)
    restored = all(_digest(SRC / name) == before[name] for name in originals)
    failed = FAILED.findall(proc.stdout)
    summary = SUMMARY.findall(proc.stdout)
    print(f"{m.mid}: {m.what}  [{m.tests}]")
    for kind, test in failed:
        print(f"    {kind} {test}")
    print(f"    result: {summary[-1][0] if summary else proc.stdout.strip().splitlines()[-1:]}")
    print(f"    caught: {bool(failed)}; restored byte-for-byte: {restored}")
    return bool(failed) and restored


def main() -> int:
    results = [run_one(m) for m in MUTATIONS]
    print(f"SUMMARY: {sum(results)} of {len(results)} mutations caught, every file restored")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
