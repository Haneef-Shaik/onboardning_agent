"""MCP-1 mutation checks: break each guard on purpose, run the tests, record which named tests fail,
then restore the file byte for byte. Run from the repo root:

    uv run --frozen python docs/evidence/MCP-1/mutate.py > docs/evidence/MCP-1/mutation-checks.txt
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
INT = "apps/mcp-mongo/tests_int"


@dataclass(frozen=True)
class Mutation:
    mid: str
    what: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old, new)
    tests: str = UNIT


MUTATIONS = [
    Mutation("M1", "find limit maximum raised to 1000 (box 2)", (("contracts.py", "MAX_FIND_LIMIT = 100", "MAX_FIND_LIMIT = 1000"),)),
    Mutation(
        "M2",
        "find limit made optional, default 100 (box 2)",
        (("contracts.py", 'description="Required, 1-100")]', 'description="Required, 1-100")] = 100'),),
    ),
    Mutation("M3", "org_id rejection disabled (box 3)", (("policy.py", "    if _mentions_org(value):", "    if False:"),)),
    Mutation(
        "M4",
        "org_id rejection checks the top level only (box 3)",
        (
            ("policy.py", " or _mentions_org(v) for k, v in node.items())", " for k, v in node.items())"),
            ("policy.py", "        return any(_mentions_org(item) for item in node)", "        return False"),
        ),
    ),
    Mutation(
        "M5",
        "insert takes org_id from the document instead of the context (box 3)",
        (
            ("tools.py", '    policy.reject_org_id(document, "document")\n', ""),
            (
                "tools.py",
                "    with tenant_scope(ctx.org_id):\n        inserted",
                '    with tenant_scope(str(document.get("org_id", ctx.org_id))):\n        inserted',
            ),
        ),
    ),
    Mutation(
        "M6",
        "tool arguments no longer closed: extra='ignore' (boxes 2-3, contracts)",
        (("contracts.py", 'extra="forbid", arbitrary_types_allowed=False', 'extra="ignore", arbitrary_types_allowed=False'),),
    ),
    Mutation(
        "M7",
        "call-context signature not verified",
        (("call_context.py", 'options={"require": _REQUIRED}', 'options={"require": _REQUIRED, "verify_signature": False}'),),
    ),
    Mutation("M8", "write allowed on readable collections", (("policy.py", "if collection not in ctx.write:", "if collection not in ctx.readable:"),)),
    Mutation("M9", "$lookup added to the stage allowlist", (("policy.py", '"$addFields"})', '"$addFields", "$lookup"})'),)),
    Mutation(
        "M10",
        "span loses org_id (box 4)",
        (("server.py", 'span.set_attributes({"org_id": ctx.org_id, "agent.name"', 'span.set_attributes({"agent.name"'),),
    ),
    Mutation(
        "M11",
        "span loses the result count (box 4)",
        (("server.py", '{"outcome": "ok", "db.result_count": _result_count(result)}', '{"outcome": "ok"}'),),
    ),
    Mutation(
        "M12",
        "span carries the arguments, i.e. document content (box 4)",
        (
            (
                "server.py",
                '{"outcome": "ok", "db.result_count": _result_count(result)}',
                '{"outcome": "ok", "db.result_count": _result_count(result), "db.args": repr(arguments)}',
            ),
        ),
    ),
    Mutation(
        "M13",
        "error span records the exception message (box 4)",
        (("server.py", "span.set_status(Status(StatusCode.ERROR))", "span.set_status(Status(StatusCode.ERROR, str(exc)))"),),
    ),
    Mutation(
        "M14",
        "validation errors echo the submitted values",
        (("server.py", "_validation_message(exc)) from None", "str(exc)) from None"),),
    ),
    Mutation("M15", "org_id rejection disabled, against testcontainers Mongo (box 3, int)", (("policy.py", "    if _mentions_org(value):", "    if False:"),), INT),
    Mutation("M16", "find limit maximum raised to 1000, against testcontainers Mongo (box 2, int)", (("contracts.py", "MAX_FIND_LIMIT = 100", "MAX_FIND_LIMIT = 1000"),), INT),
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
            if text.count(old) < 1:
                print(f"{m.mid}: edit target not found in {name}: {old!r}")
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
