#!/usr/bin/env python3
"""LLM-1 mutation checks: break each guard on purpose, run the py-llm unit tests, record what failed.

Run from the repo root: `python3 docs/evidence/LLM-1/mutate.py > docs/evidence/LLM-1/mutation-checks.txt`.
Every file is restored byte-for-byte afterwards; exit 1 if any mutation survives or a restore fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "libs/py-llm/src/onb_llm"
FAILED = re.compile(r"^FAILED (\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Mutation:
    key: str
    what: str
    path: Path
    old: str
    new: str


MUTATIONS = (
    Mutation(
        "M1",
        "org override checked before the manifest",
        SRC / "resolution.py",
        "    if manifest is not None:\n        selection, source = manifest, \"manifest\"\n"
        "    elif org_override is not None:\n        selection, source = org_override, \"org_override\"\n",
        "    if org_override is not None:\n        selection, source = org_override, \"org_override\"\n"
        "    elif manifest is not None:\n        selection, source = manifest, \"manifest\"\n",
    ),
    Mutation(
        "M2",
        "org override ignored (falls through to the default)",
        SRC / "resolution.py",
        "    elif org_override is not None:",
        "    elif False:",
    ),
    Mutation(
        "M3",
        "default ignored: no selection resolves to the first alias",
        SRC / "resolution.py",
        "        selection, source = config.default, \"default\"",
        "        selection, source = LLMSelection(alias=sorted(config.aliases)[0]), \"default\"",
    ),
    Mutation(
        "M4",
        "discovery hard-codes the built-ins instead of reading entry points",
        SRC / "registry.py",
        "    found = metadata.entry_points(group=ENTRY_POINT_GROUP) if entry_points is None else entry_points",
        "    found = [e for e in metadata.entry_points(group=ENTRY_POINT_GROUP) "
        "if e.value.startswith('onb_llm.')] if entry_points is None else entry_points",
    ),
    Mutation(
        "M5",
        "entry-point name / connector name mismatch accepted",
        SRC / "registry.py",
        "    if connector.name != entry_point.name:",
        "    if False:",
    ),
    Mutation(
        "M6",
        "required env never checked",
        SRC / "runtime.py",
        "        if missing:\n            raise ConnectorEnvironmentError",
        "        if False:\n            raise ConnectorEnvironmentError",
    ),
    Mutation(
        "M7",
        "a blank required variable counts as set",
        SRC / "runtime.py",
        'if not self._env.get(var, "").strip()',
        "if var not in self._env",
    ),
    Mutation(
        "M8",
        "startup skips the configured connectors (checked only on resolve)",
        SRC / "runtime.py",
        "            runtime._check(connector, selected_by)",
        "            pass",
    ),
    Mutation(
        "M9",
        "a required ${VAR} that is unset becomes empty",
        SRC / "interpolation.py",
        "        raise LLMConfigError(f\"{where} needs environment variable",
        "        return \"\"\n        raise LLMConfigError(f\"{where} needs environment variable",
    ),
    Mutation(
        "M10",
        "fake model repeats its last turn instead of failing when the script runs out",
        SRC / "connectors/fake.py",
        "            if turn >= len(self.script):",
        "            if turn >= len(self.script) and not self.script:",
    ),
    Mutation(
        "M11",
        "fake model accepts a scripted call to a tool that was not bound",
        SRC / "connectors/fake.py",
        "        if call[\"name\"] not in bound:",
        "        if False:",
    ),
    Mutation(
        "M12",
        "no LLM span handler attached to the chat model",
        SRC / "runtime.py",
        "        return created.model_copy(update={\"callbacks\": [*existing, handler]})",
        "        return created.model_copy(update={\"callbacks\": existing})",
    ),
    Mutation(
        "M13",
        "the error span carries the exception message",
        SRC / "telemetry.py",
        "        kind = type(error).__name__",
        "        kind = f\"{type(error).__name__}: {error}\"",
    ),
    Mutation(
        "M14",
        "a model id is written in code",
        SRC / "connectors/bedrock.py",
        "_GUARDRAIL_PARAMS = (",
        "_FALLBACK_MODEL = \"anthropic.claude-3-haiku-20240307-v1:0\"\n_GUARDRAIL_PARAMS = (",
    ),
    Mutation(
        "M15",
        "bedrock no longer requires AWS_REGION",
        SRC / "connectors/bedrock.py",
        "    required_env: tuple[str, ...] = (\"AWS_REGION\",)",
        "    required_env: tuple[str, ...] = ()",
    ),
)


def run_tests() -> str:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "libs/py-llm/tests", "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def main() -> int:
    survived = 0
    for mutation in MUTATIONS:
        original = mutation.path.read_bytes()
        text = original.decode()
        if text.count(mutation.old) != 1:
            print(f"{mutation.key}: anchor not found exactly once in {mutation.path.relative_to(ROOT)}")
            return 1
        try:
            mutation.path.write_text(text.replace(mutation.old, mutation.new), encoding="utf-8")
            output = run_tests()
        finally:
            mutation.path.write_bytes(original)
        if mutation.path.read_bytes() != original:
            print(f"{mutation.key}: RESTORE FAILED for {mutation.path}")
            return 1
        failed = FAILED.findall(output)
        summary = output.strip().splitlines()[-1] if output.strip() else "(no output)"
        status = "CAUGHT" if failed else "SURVIVED"
        survived += not failed
        print(f"{mutation.key} {status}: {mutation.what} ({mutation.path.relative_to(ROOT)})")
        for name in failed:
            print(f"    FAILED {name}")
        print(f"    result: {summary}")
    print(f"\n{len(MUTATIONS) - survived} of {len(MUTATIONS)} mutations caught; all files restored.")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
