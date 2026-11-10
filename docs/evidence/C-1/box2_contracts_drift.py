"""C-1 box 2 evidence: `make contracts-check` fails after a model change without regeneration, and
passes after `make contracts`. Restores every touched file byte for byte and checks it did.

    uv run --frozen python docs/evidence/C-1/box2_contracts_drift.py > docs/evidence/C-1/box2-contracts-check.txt
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "libs/py-contracts/src/onb_contracts/health.py"
ARTEFACTS = (ROOT / "libs/ts-api-client/openapi.json", ROOT / "libs/ts-api-client/src/generated/schema.ts")
OLD = '    status: Literal["ok"]\n    service: str\n'
NEW = '    status: Literal["ok"]\n    service: str\n    version: str = "0.1.0"\n'


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    print((done.stdout + done.stderr).rstrip())
    print(f"exit={done.returncode}\n", flush=True)
    return done.returncode


def main() -> int:
    originals = {p: p.read_bytes() for p in (MODEL, *ARTEFACTS)}
    source = MODEL.read_text(encoding="utf-8")
    if OLD not in source:
        print("the Health model changed; update this script", file=sys.stderr)
        return 1
    try:
        print("## 1. Model change: Health gains `version: str`, contracts NOT regenerated")
        MODEL.write_text(source.replace(OLD, NEW), encoding="utf-8")
        failed = run(["make", "contracts-check"])
        print("## 2. Regenerate, then check again")
        regenerated = run(["make", "contracts"])
        passed = run(["make", "contracts-check"])
    finally:
        for path, data in originals.items():
            path.write_bytes(data)
    restored = all(p.read_bytes() == data for p, data in originals.items())
    print("## 3. Restore the model and the committed artefacts")
    after = run(["make", "contracts-check"])
    print(f"restored byte-for-byte: {restored}")
    ok = failed != 0 and regenerated == 0 and passed == 0 and after == 0 and restored
    print(f"RESULT: {'as expected (fail, then pass)' if ok else 'NOT as expected'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
