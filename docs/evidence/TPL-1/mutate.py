"""TPL-1 mutation checks: break each guard on purpose, run the template tests, record what failed.

Run from the repo root: `uv run --frozen python docs/evidence/TPL-1/mutate.py`. Each mutation is a
single exact text replacement that must match once; the file is restored after each run, and the
script checks at the end that no source file differs from git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXPR = "libs/py-agents/src/onb_agents/templates/expr.py"
GRAPH = "libs/py-agents/src/onb_agents/templates/graph.py"
SENS = "libs/py-agents/src/onb_agents/templates/sensitive.py"
VAL = "libs/py-agents/src/onb_agents/templates/validator.py"
MODEL = "libs/py-contracts/src/onb_contracts/templates.py"
TESTS = [
    "libs/py-agents/tests/test_template_validator.py",
    "libs/py-agents/tests/test_when_expr.py",
    "libs/py-contracts/tests/test_template_contracts.py",
]

# (id, guard, file, old, new)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("M1", "cycles are never found", GRAPH, "    found: list[list[str]] = []\n", "    return []\n    found: list[list[str]] = []\n"),
    ("M2", "unknown agent not reported", VAL, "self._add(Rule.UNKNOWN_AGENT,", "None and self._add(Rule.UNKNOWN_AGENT,"),
    ("M3", "retired agents kept", VAL, "for a in agents if a.active}", "for a in agents}"),
    ("M4", "every tool granted", VAL, "    server, _, name = tool.rpartition(\".\")\n", "    return True\n    server, _, name = tool.rpartition(\".\")\n"),
    ("M5", "catalog agent not compared", VAL, "if entry is None or entry.agent != manifest.name:", "if entry is None:"),
    ("M6", "approval action/type not matched", VAL, "elif (entry.task_type == \"approval\") != (task.type == \"approval\"):", "elif False:"),
    ("M7", "undeclared variable in when", VAL, "self._add(Rule.UNDECLARED_VARIABLE, f\"when reads", "None and self._add(Rule.UNDECLARED_VARIABLE, f\"when reads"),
    ("M8", "undeclared approver variable", VAL, "self._add(Rule.UNDECLARED_VARIABLE, f\"approver reads", "None and self._add(Rule.UNDECLARED_VARIABLE, f\"approver reads"),
    ("M9", "undeclared sla variable", VAL, "self._add(Rule.UNDECLARED_VARIABLE, f\"sla.due reads", "None and self._add(Rule.UNDECLARED_VARIABLE, f\"sla.due reads"),
    ("M10", "undeclared inputs placeholder", VAL, "if self._variable(head) is None:", "if False:"),
    ("M11", "approval without approver accepted", VAL, "if task.type == \"approval\" and task.approver is None:", "if False:"),
    ("M12", "approver type not checked", VAL, "elif variable.type != \"user_ref\":", "elif False:"),
    ("M13", "approver on a plain task accepted", VAL, "if task.type != \"approval\" and not task.requires_approval:", "if False:"),
    ("M14", "inputs not scanned", VAL, "for where, finding in input_findings(task.inputs):", "for where, finding in ():"),
    ("M15", "any key exempts a sensitive value", SENS, "not (key == \"channel\" and", "not (True and"),
    ("M16", "raw PII not detected", SENS, "found = [f\"a raw {span.type.value.lower()} value\" for span in detect(text)]", "found: list[str] = []"),
    ("M17", "vault tokens not detected", SENS, "    if _TOKEN.search(folded):\n", "    if False:\n"),
    ("M18", "vault names not detected", SENS, "    if names_a_forbidden_collection(folded):\n", "    if False:\n"),
    ("M19", "sensitive keys not detected", SENS, "                if names_a_sensitive_field(str(child_key)):\n", "                if False:\n"),
    ("M20", "placeholders and dotted paths not detected", SENS, "return [\"a reference to a sensitive field\"] if _sensitive_path(text) else []", "return []"),
    ("M21", "sensitive variable names accepted", VAL, "if names_a_sensitive_field(variable.name):", "if False:"),
    ("M22", "sink policy not consulted", VAL, "elif not policy.allows(vault_field, access.sink):", "elif False:"),
    ("M23", "never-sinkable floor and policy both off", VAL, "elif vault_field in NEVER_SINKABLE:\n                self._add(Rule.PII_ACCESS_FORBIDDEN, f\"pii_access names {name}: no agent sink may ever resolve it\")\n            elif not policy.allows(vault_field, access.sink):", "elif False:\n                pass\n            elif False:"),
    ("M24", "sink server not checked against the agent", VAL, "if manifest is not None and server not in manifest.mcp:", "if False:"),
    ("M25", "unknown dependency accepted", VAL, "if self._template.task(dependency) is None:", "if False:"),
    ("M26", "enum literal not checked", VAL, "elif variable.values is not None and literal not in variable.values:", "elif False:"),
    ("M27", "literal type not checked", VAL, "if (isinstance(literal, bool) and bool not in kinds) or not isinstance(literal, kinds):", "if False:"),
    ("M28", "sla variable type not checked", VAL, "elif variable.type != \"date\":", "elif False:"),
    ("M29", "function-call guard off", EXPR, "if callable_before and after is not None and after.text == \"(\" and token.text not in _KEYWORDS:", "if False:"),
    ("M30", "attribute guard off", EXPR, "            raise ExpressionError(ExpressionReason.ATTRIBUTE_ACCESS, f\"attribute access is not allowed (at {token.at})\")", "            continue"),
    ("M31", "import guard off", EXPR, "_IMPORT_NAMES: Final = frozenset({\"import\", \"from\", \"__import__\", \"importlib\"})", "_IMPORT_NAMES: Final = frozenset[str]()"),
    ("M32", "dunder guard off", EXPR, "if token.text in _FORBIDDEN_NAMES or token.text.startswith(\"__\"):", "if token.text in _FORBIDDEN_NAMES:"),
    ("M33", "subscript guard off", EXPR, "if callable_before and after is not None and after.text == \"[\" and token.text not in _KEYWORDS:", "if False:"),
    ("M34", "depth limit off", EXPR, "        if self._depth > MAX_DEPTH:\n", "        if False:\n"),
    ("M35", "length limit off", EXPR, "    if len(source) > MAX_LENGTH:\n", "    if False:\n"),
    ("M36", "booleans equal numbers", EXPR, "return _kind(left) == _kind(right) and left == right", "return left == right"),
    ("M37", "non-boolean condition accepted", EXPR, "        if not isinstance(value, bool):\n            raise ExpressionError(ExpressionReason.EVALUATION, \"a condition must be true or false\")\n        return value", "        return bool(value)"),
    ("M38", "missing variable reads as null", EXPR, "            if node.name not in self._variables:\n", "            if False:\n"),
    ("M39", "errors echo the submitted value", MODEL, "_FROZEN = ConfigDict(frozen=True, extra=\"forbid\", hide_input_in_errors=True)", "_FROZEN = ConfigDict(frozen=True, extra=\"forbid\")"),
    ("M40", "unique task ids not enforced", MODEL, "        _unique(tuple(task.id for task in self.tasks), \"task ids\")\n", ""),
    ("M41", "no unicode folding", SENS, "    return skeleton(text).text\n", "    return text\n"),
    ("M42", "whole-name match only (no parts)", SENS, "    return _PART_PATTERN.search(_normal(text)) is not None\n", "    return _normal(text) in SENSITIVE_NAMES\n"),
    ("M43", "no camelCase split", SENS, "    split = _CAMEL.sub(\"_\", fold(text).strip())\n", "    split = fold(text).strip()\n"),
    ("M44", "numbers in inputs not scanned", SENS, "        elif isinstance(value, int | float) and not isinstance(value, bool):\n", "        elif False:\n"),
    ("M45", "only dotted paths", SENS, "_PATH_SEPARATORS: Final = re.compile(r\"\"\"[./\\[\\]\"']+\"\"\")", "_PATH_SEPARATORS: Final = re.compile(r\"[.]+\")"),
    ("M46", "identifiers and approver refs not scanned", VAL, "for where, text in _strings(task.model_dump(mode=\"json\", exclude={\"inputs\"}), \"\"):", "for where, text in ((\"title\", task.title), (\"when\", task.when or \"\")):"),
    ("M47", "input string length not capped", MODEL, "        if len(value) > MAX_INPUT_STRING:\n", "        if False:\n"),
    ("M48", "input total size not capped", MODEL, "        if _size(value) > MAX_INPUTS_SIZE:\n", "        if False:\n"),
    ("M49", "input keys not restricted", MODEL, "InputKey = Annotated[StrictStr, Field(pattern=r\"^[A-Za-z][A-Za-z0-9_-]{0,63}$\")]", "InputKey = Annotated[StrictStr, Field(min_length=1, max_length=64)]"),
    ("M50", "repeated values echoed", MODEL, "raise ValueError(f\"{what}: item {index} repeats an earlier item\")", "raise ValueError(f\"{what}: item {index} repeats {value}\")"),
    ("M51", "schema loc names input keys", VAL, "        if \"inputs\" in loc:", "        if False:"),
    ("M52", "cycle message not redacted", VAL, "redact(\"tasks form a cycle: \" + \" -> \".join(loop))", "\"tasks form a cycle: \" + \" -> \".join(loop)"),
    ("M53", "input keys named by text", SENS, "                child_where = f\"{where}[{index}]\"\n", "                child_where = f\"{where}.{child_key}\"\n"),
    ("M54", "non-ASCII digits accepted", EXPR, "      | (?P<num>-?[0-9]+)", "      | (?P<num>-?\\d+)"),
    ("M55", "variable-vs-variable types not checked", VAL, "and _KIND[first.type] != _KIND[second.type]:", "and False:"),
    ("M56", "lists unbounded", MODEL, "    variables: tuple[Variable, ...] = Field(default=(), max_length=MAX_LIST)", "    variables: tuple[Variable, ...] = ()"),
]


def _failed(output: str) -> list[str]:
    return [line.split(" ", 1)[1].split(" - ")[0] for line in output.splitlines() if line.startswith("FAILED ")]


def main() -> int:
    survivors = []
    for mutation_id, guard, rel, old, new in MUTATIONS:
        path = REPO / rel
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            print(f"{mutation_id} SETUP ERROR: pattern found {source.count(old)} times in {rel}")
            return 2
        path.write_text(source.replace(old, new), encoding="utf-8")
        try:
            run = subprocess.run(
                ["uv", "run", "--frozen", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "-rf", "--no-header"],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            path.write_text(source, encoding="utf-8")
        failed = _failed(run.stdout)
        summary = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip()[-200:]
        if failed or run.returncode != 0:
            shown = ", ".join(failed[:3]) + (f" (+{len(failed) - 3} more)" if len(failed) > 3 else "")
            print(f"{mutation_id} KILLED  {guard}: {shown or summary}")
        else:
            survivors.append(mutation_id)
            print(f"{mutation_id} SURVIVED {guard}: {summary}")
    diff = subprocess.run(["git", "diff", "--stat", "--", "libs"], cwd=REPO, capture_output=True, text=True, check=True)
    changed = [line for line in diff.stdout.splitlines() if "|" in line]
    print(f"source diff after all mutations: {len(changed)} files differ")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 1 if survivors or changed else 0


if __name__ == "__main__":
    sys.exit(main())
