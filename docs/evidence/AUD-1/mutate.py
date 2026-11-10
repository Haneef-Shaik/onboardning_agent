"""AUD-1 mutation checks: break each guard on purpose, run the unit tests, record which tests fail.

Run from the repo root on a clean tree: `uv run --frozen python docs/evidence/AUD-1/mutate.py`.
Each mutation is one exact text replacement (it must match once); the file is restored from git after.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

DATA = "libs/py-data/src/onb_data"
API = "apps/api/src/onb_api"
TESTS = ["apps/api/tests", "libs/py-contracts/tests", "libs/py-data/tests"]
FAILED = re.compile(r"^FAILED (\S+)", re.MULTILINE)

MUTATIONS = [
    (
        "M1 box 1: store.tenant() hands out audit_log with its update/delete path",
        f"{DATA}/store.py",
        '        if name in APPEND_ONLY_COLLECTIONS:\n            raise UnknownCollectionError(f"{name} is append-only',
        '        if False:\n            raise UnknownCollectionError(f"{name} is append-only',
    ),
    (
        "M2 box 1: the append-only repository gains a delete",
        f"{DATA}/append_only.py",
        "    async def count(",
        "    async def delete_one(self, query: Mapping[str, Any]) -> Any:\n"
        "        return await self._collection.delete_one(query)\n\n"
        "    async def count(",
    ),
    (
        "M3 box 1: the audit log gains an update",
        f"{DATA}/audit.py",
        "    async def ensure_indexes(self) -> None:",
        "    async def update(self, record_id: str, reason: str) -> None:\n"
        "        raise NotImplementedError\n\n"
        "    async def ensure_indexes(self) -> None:",
    ),
    (
        "M4 box 1: the audit log hands out its repository",
        f"{DATA}/audit.py",
        '    __slots__ = ("_clock", "_repo", "_store", "_trace_id")',
        '    __slots__ = ("_clock", "_repo", "_store", "_trace_id")\n\n'
        "    @property\n"
        "    def repository(self) -> object:\n"
        "        return self._repo",
    ),
    (
        "M5 box 3: the record ignores the current span",
        f"{DATA}/audit.py",
        '            "trace_id": self._trace_id(),',
        '            "trace_id": None,',
    ),
    (
        "M6 box 3: an invalid span context still gives a trace id",
        f"{DATA}/audit.py",
        'return f"{context.trace_id:032x}" if context.is_valid else None',
        'return f"{context.trace_id:032x}"',
    ),
    (
        "M7 box 2: HR is let into GET /audit",
        f"{API}/audit/routes.py",
        "HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]",
        "HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR))]",
    ),
    (
        "M8 box 2: every role is let into GET /audit",
        f"{API}/audit/routes.py",
        "HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]",
        "HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR, Role.MANAGER))]",
    ),
    (
        "M9 org scope: reads not scoped to the org",
        f"{DATA}/append_only.py",
        "        scoped = scoping.scoped_filter(query, current_tenant().org_id)\n        cursor =",
        "        scoped = dict(query or {})\n        cursor =",
    ),
    (
        "M10 org scope: the record is written without the org",
        f"{DATA}/append_only.py",
        "        prepared = scoping.scoped_document(document, current_tenant().org_id)",
        "        prepared = dict(document)",
    ),
    (
        "M11 filters: since after until accepted",
        f"{API}/audit/routes.py",
        "    if since is not None and until is not None and since > until:",
        "    if False:",
    ),
    (
        "M12 ordering: oldest first",
        f"{DATA}/audit.py",
        'sort=[("at", -1), ("_id", -1)]',
        'sort=[("at", 1), ("_id", 1)]',
    ),
]


def run_once(label: str, path: str, old: str, new: str) -> bool:
    file = pathlib.Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        print(f"{label}: SETUP ERROR, pattern found {text.count(old)} times")
        return False
    file.write_text(text.replace(old, new))
    try:
        result = subprocess.run(
            ["uv", "run", "--frozen", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)
    failed = FAILED.findall(result.stdout)
    print(f"{label}\n  {path}: {old.strip()!r} -> {new.strip()!r}")
    print(f"  caught: {'yes' if failed else 'NO'} ({len(failed)} failed)")
    for name in failed[:6]:
        print(f"    FAILED {name}")
    if len(failed) > 6:
        print(f"    ... and {len(failed) - 6} more")
    return bool(failed)


def main() -> int:
    caught = [run_once(*mutation) for mutation in MUTATIONS]
    diff = subprocess.run(["git", "status", "--porcelain", "--", "libs", "apps"], capture_output=True, text=True)
    print(f"\n{sum(caught)}/{len(caught)} mutations caught")
    print(f"worktree changes under libs/ and apps/ after all mutations: {diff.stdout.strip() or 'none'}")
    return 0 if all(caught) else 1


if __name__ == "__main__":
    sys.exit(main())
