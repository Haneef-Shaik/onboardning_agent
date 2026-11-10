"""MCP-2 mutation checks: break each validator guard on purpose, run the unit tests, record which
named tests fail, then restore the file byte for byte. Run from the repo root:

    uv run --frozen python docs/evidence/MCP-2/mutate.py > docs/evidence/MCP-2/mutation-checks.txt

`--tests <path> --only V2,V4` runs only those mutations against only those tests (used to show
the box-2 property tests alone catch the operator guards).
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
TESTS = sys.argv[sys.argv.index("--tests") + 1] if "--tests" in sys.argv else UNIT
ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
FAILED = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)
SUMMARY = re.compile(r"^=+ (.*(passed|failed).*) =+$", re.MULTILINE)
V = "validator.py"
RULES = "validator_rules.py"
OP_FORBIDDEN = '        if name in FORBIDDEN_OPERATORS:\n            raise self.fail("forbidden_operator", name)\n'


@dataclass(frozen=True)
class Mutation:
    mid: str
    what: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old, new)


MUTATIONS = [
    Mutation("V1", "forbidden operators no longer named: only 'not allowed' (box 1)", ((RULES, OP_FORBIDDEN, ""),)),
    Mutation(
        "V2",
        "$where put on the field-operator allowlist and the denylist check removed (boxes 1-2)",
        (
            (RULES, '"$elemMatch", "$mod"),', '"$elemMatch", "$mod", "$where"),'),
            (RULES, OP_FORBIDDEN, ""),
        ),
    ),
    Mutation(
        "V3",
        "$expr contents not walked (boxes 1-2: $expr with JS)",
        ((RULES, '            elif name == "$expr":\n                self.expression(value, depth + 1)', '            elif name == "$expr":\n                pass'),),
    ),
    Mutation(
        "V4",
        "expression operators not checked against the allowlist (boxes 1-2)",
        ((RULES, "self.key(name, EXPRESSION_OPERATORS) and name in ARRAY_ITERATORS", "name in ARRAY_ITERATORS"),),
    ),
    Mutation(
        "V5",
        "literal values (inserts, $set values) may carry operators (box 1)",
        ((RULES, "                self.key(name, frozenset())\n", "                pass\n"),),
    ),
    Mutation(
        "V6",
        "field conditions accept any operator (box 1)",
        ((RULES, "            operator = self.operator(name, FIELD_OPERATORS)", "            operator = str(name)"),),
    ),
    Mutation("V7", "$match body not walked (box 1)", ((V, "    walk.query(_mapping(walk, body), depth)", "    _mapping(walk, body)"),)),
    Mutation(
        "V8",
        "$group accumulators not checked (box 1: $accumulator)",
        ((V, "        walk.operator(operator, ACCUMULATOR_OPERATORS)\n", ""),),
    ),
    Mutation(
        "V9",
        "forbidden stage names reach only the stage table check (box 1)",
        ((V, '        if name in FORBIDDEN_OPERATORS:\n            raise walk.fail("forbidden_operator", name)\n', ""),),
    ),
    Mutation("V10", "update operators not checked (box 1)", ((V, "        walk.operator(operator, UPDATE_OPERATORS)\n", ""),)),
    Mutation(
        "V11",
        "$pull condition not walked (box 1)",
        ((V, '    elif operator == "$pull" and isinstance(value, Mapping):\n        walk.element_match(value, 3)', '    elif operator == "$pull":\n        pass'),),
    ),
    Mutation("V12", "pii.* writable (box 3)", ((V, "    if first == PII_FIELD:", "    if first is None:"),)),
    Mutation("V13", "org_id allowed as a field in the validator (box 3, validator layer)", ((RULES, "        if segments[0] == ORG_FIELD:", "        if False:"),)),
    Mutation(
        "V14",
        "$-led path segments allowed (box 1: dotted)",
        ((RULES, '            if not segment or (segment.startswith("$") and not allowed_positional):', "            if not segment:"),),
    ),
    Mutation("V15", "NFKC canonical-name check removed (boxes 1-2: unicode)", ((RULES, '        if unicodedata.normalize("NFKC", name) != name:', "        if False:"),)),
    Mutation("V16", "invisible/control characters allowed in names (box 1: unicode)", ((RULES, '_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})', "_INVISIBLE = frozenset()"),)),
    Mutation("V17", "depth cap removed (box 1: nested)", ((RULES, "        if depth > MAX_DEPTH:", "        if depth > 10**6:"),)),
    Mutation("V18", "node-count cap removed (box 1)", ((RULES, "self.nodes > MAX_NODES or", "self.nodes > 10**9 or"),)),
    Mutation("V19", "64-bit integer cap removed (review)", ((RULES, "        too_big_int = isinstance(node, int) and not -_INT64 <= node < _INT64", "        too_big_int = False"),)),
    Mutation("V20", "$$ system-variable allowlist removed (review)", ((RULES, "not variable[:1].islower() and variable not in SYSTEM_VARIABLES", "False"),)),
    Mutation("V21", "\"$path\" strings in expressions not checked: \"$org_id\" readable by name (review)", ((RULES, "        elif value.startswith(\"$\"):\n            self.field(value[1:])", "        elif value.startswith(\"$\"):\n            pass"),)),
    Mutation("V22", "$map/$filter nesting cap removed (review)", ((RULES, "MAX_ARRAY_ITERATOR_NESTING = 2", "MAX_ARRAY_ITERATOR_NESTING = 99"),)),
    Mutation("V23", "positional allowed as the first path segment (review)", ((RULES, "positional and index > 0 and segment", "positional and segment"),)),
    Mutation("V24", "_id writable by update (review)", ((V, "    if first == ID_FIELD:", "    if first is None:"),)),
    Mutation("V25", "validator not wired into the server (boxes 1, 3)", (("server.py", "            validator.check_call(self.name, args)", "            pass"),)),
    Mutation(
        "V26",
        "a delete_one tool added to the served tools (box 4)",
        (
            ("server.py", '    "update_one": tools.update_one,\n}', '    "update_one": tools.update_one,\n    "delete_one": tools.update_one,\n}'),
            (
                "contracts.py",
                "CONTRACTS: dict[str, ToolContract] = {\n",
                'CONTRACTS: dict[str, ToolContract] = {\n    "delete_one": ToolContract(UpdateOneArgs, UpdateResult, "x", read_only=False),\n',
            ),
        ),
    ),
    Mutation(
        "V27",
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
            ["uv", "run", "--frozen", "pytest", TESTS, "-q", "-p", "no:cacheprovider", "-rfE", "--no-header"],
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
    results = [run_one(m) for m in MUTATIONS if ONLY is None or m.mid in ONLY]
    print(f"SUMMARY: {sum(results)} of {len(results)} mutations caught, every file restored")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
