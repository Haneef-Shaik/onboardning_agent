"""MCP-2 mutation checks: break each validator guard on purpose, run the unit tests, record which
named tests fail, then restore the file byte for byte. Run from the repo root:

    uv run --frozen python docs/evidence/MCP-2/mutate.py > docs/evidence/MCP-2/mutation-checks.txt
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
FAILED = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)
SUMMARY = re.compile(r"^=+ (.*(passed|failed).*) =+$", re.MULTILINE)
V = "validator.py"


@dataclass(frozen=True)
class Mutation:
    mid: str
    what: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old, new)


MUTATIONS = [
    Mutation(
        "V1",
        "forbidden operators no longer named: treated as merely unknown (box 1)",
        ((V, "        if name in FORBIDDEN_OPERATORS:\n            raise self.fail(\"forbidden_operator\", name)\n", ""),),
    ),
    Mutation(
        "V2",
        "$where put on the query allowlist and the denylist check removed (boxes 1-2)",
        (
            (V, '        "$expr",\n    }', '        "$expr",\n        "$where",\n    }'),
            (V, "        if name in FORBIDDEN_OPERATORS:\n            raise self.fail(\"forbidden_operator\", name)\n", ""),
        ),
    ),
    Mutation(
        "V3",
        "$expr contents not walked (boxes 1-2: $expr with JS)",
        ((V, "                    self.expression(value, depth + 1)\n                else:", "                    pass\n                else:"),),
    ),
    Mutation(
        "V4",
        "expression operators not checked against the allowlist (boxes 1-2)",
        ((V, "                self.key(name, EXPRESSION_OPERATORS)\n", "                pass\n"),),
    ),
    Mutation(
        "V5",
        "literal values (inserts, $set values) may carry operators (box 1)",
        ((V, "                self.key(name, frozenset())\n", "                pass\n"),),
    ),
    Mutation(
        "V6",
        "$match body not walked (box 1)",
        ((V, "    walk.query(_mapping(walk, body), depth)", "    _mapping(walk, body)"),),
    ),
    Mutation(
        "V7",
        "$group accumulators not checked (box 1: $accumulator)",
        ((V, "        walk.operator(operator, ACCUMULATOR_OPERATORS)\n", ""),),
    ),
    Mutation(
        "V8",
        "forbidden stage name allowed through to the stage table check only (box 1)",
        ((V, "        if name in FORBIDDEN_OPERATORS:\n            raise walk.fail(\"forbidden_operator\", name)\n", ""),),
    ),
    Mutation(
        "V9",
        "update operators not checked against the allowlist (box 1)",
        ((V, "        walk.operator(operator, UPDATE_OPERATORS)\n", ""),),
    ),
    Mutation(
        "V10",
        "$pull condition not walked (box 1)",
        ((V, "                walk.query(value, 3)\n", "                pass\n"),),
    ),
    Mutation(
        "V11",
        "pii.* writable (box 3)",
        ((V, "    if walk.field(name, positional=True)[0] == PII_FIELD:", "    if walk.field(name, positional=True)[0] == None:"),),
    ),
    Mutation(
        "V12",
        "org_id allowed as a field in the validator (box 3, validator layer)",
        ((V, "        if segments[0] == ORG_FIELD:\n", "        if False:\n"),),
    ),
    Mutation(
        "V13",
        "$-led path segments allowed (box 1: dotted)",
        ((V, '            if not segment or (segment.startswith("$") and not (positional and segment in _POSITIONAL)):', "            if not segment:"),),
    ),
    Mutation(
        "V14",
        "NFKC canonical-name check removed (boxes 1-2: unicode)",
        ((V, '        if unicodedata.normalize("NFKC", name) != name:', "        if False:"),),
    ),
    Mutation(
        "V15",
        "invisible/control characters allowed in names (box 1: unicode)",
        ((V, '{"Cc", "Cf", "Cs", "Co", "Cn"}', "set()"),),
    ),
    Mutation(
        "V16",
        "depth cap removed (box 1: nested)",
        ((V, "        if depth > MAX_DEPTH:", "        if depth > 10**6:"),),
    ),
    Mutation(
        "V17",
        "size cap removed (box 1)",
        ((V, "        if self.nodes > MAX_NODES:", "        if self.nodes > 10**9:"),),
    ),
    Mutation(
        "V18",
        "validator not wired into the server (boxes 1, 3)",
        (("server.py", "            validator.check_call(self.name, args)", "            pass"),),
    ),
    Mutation(
        "V19",
        "a delete_one tool added to the served tools (box 4)",
        (
            (
                "server.py",
                '    "update_one": tools.update_one,\n}',
                '    "update_one": tools.update_one,\n    "delete_one": tools.update_one,\n}',
            ),
            (
                "contracts.py",
                "CONTRACTS: dict[str, ToolContract] = {\n",
                'CONTRACTS: dict[str, ToolContract] = {\n    "delete_one": ToolContract(UpdateOneArgs, UpdateResult, "x", read_only=False),\n',
            ),
        ),
    ),
    Mutation(
        "V20",
        "a driver drop call added to the tools module (box 4)",
        (("tools.py", "    return CollectionsResult(collections=sorted(ctx.readable))", "    store.drop(ctx)\n    return CollectionsResult(collections=sorted(ctx.readable))"),),
    ),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_one(m: Mutation) -> bool:
    names = {name for name, _, _ in m.edits}
    originals = {name: (SRC / name).read_bytes() for name in names}
    before = {name: _digest(SRC / name) for name in names}
    try:
        for name, old, new in m.edits:
            text = (SRC / name).read_text()
            if text.count(old) != 1:
                print(f"{m.mid}: edit target not found exactly once in {name}: {old!r}")
                return False
            (SRC / name).write_text(text.replace(old, new, 1))
        proc = subprocess.run(
            ["uv", "run", "--frozen", "pytest", UNIT, "-q", "-p", "no:cacheprovider", "-rfE", "--no-header"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        for name, data in originals.items():
            (SRC / name).write_bytes(data)
    restored = all(_digest(SRC / name) == before[name] for name in names)
    failed = FAILED.findall(proc.stdout)
    summary = SUMMARY.findall(proc.stdout)
    print(f"{m.mid}: {m.what}")
    shown = failed[:12]
    for kind, test in shown:
        print(f"    {kind} {test}")
    if len(failed) > len(shown):
        print(f"    ... and {len(failed) - len(shown)} more")
    print(f"    result: {summary[-1][0] if summary else proc.stdout.strip().splitlines()[-1:]}")
    print(f"    caught: {bool(failed)}; restored byte-for-byte: {restored}")
    return bool(failed) and restored


def main() -> int:
    results = [run_one(m) for m in MUTATIONS]
    print(f"SUMMARY: {sum(results)} of {len(results)} mutations caught, every file restored")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
