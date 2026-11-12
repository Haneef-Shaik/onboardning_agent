"""UI-5 mutation checks: break each guard on purpose, run the named test, record which failed, restore.

    uv run --frozen python docs/evidence/UI-5/mutate.py --write

Each mutation edits one source file (string replace, asserted to match once), runs the check, and
restores the file with `git checkout`. A mutation is caught when the check fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV = {**os.environ, "E2E_API_PORT": "19810", "E2E_WEB_PORT": "19811"}
PW = ["npx", "playwright", "test", "--config", "apps/web/playwright.config.ts"]
VT = ["npx", "vitest", "run", "--config", "apps/web/vitest.config.ts"]
PY = ["uv", "run", "--frozen", "pytest", "-q", "-x"]

S = "apps/web/src/runs/"
MUTATIONS = [
    ("M1 the stream never tells the screen to re-read", S + "useRunStream.ts",
     "latest.current.onChange();\n            }\n          }", "}\n          }", PW + ["runs.spec.ts", "-g", "updates live"]),
    ("M2 a failed task hides its reason", S + "RunDetail.tsx", "{failed && (\n          <p data-testid=\"blocked-reason\"", "{false && (\n          <p data-testid=\"blocked-reason\"", PW + ["runs.spec.ts", "-g", "blocked task shows"]),
    ("M3 complete works without a reason", S + "RunDetail.tsx", 'disabled={busy || reason.trim() === ""}', "disabled={busy}", PW + ["runs.spec.ts", "-g", "completed by hand"]),
    ("M4 the trace link points at another id", S + "policy.ts", "/trace/${traceId}`", "/trace/${traceId.split('').reverse().join('')}`", PW + ["runs.spec.ts", "-g", "trace link"]),
    ("M5 managers get run controls", S + "policy.ts", 'export function canControlRuns(role: Role): boolean {\n  return STAFF.includes(role);', 'export function canControlRuns(role: Role): boolean {\n  return role !== undefined;', PW + ["runs.spec.ts", "-g", "manager sees"]),
    ("M6 managers get the trace link", S + "policy.ts", 'export function canOpenTrace(role: Role): boolean {\n  return STAFF.includes(role);', 'export function canOpenTrace(role: Role): boolean {\n  return role !== undefined;', PW + ["runs.spec.ts", "-g", "manager sees"]),
    ("M7 a timeout end stops the stream instead of reconnecting", S + "useRunStream.ts", 'if (body.reason !== "timeout") return setState("ended");', 'return setState("ended");', VT + ["src/runs/useRunStream.test.tsx"]),
    ("M8 a dropped connection is not retried", S + "useRunStream.ts", 'if (!timedOut) throw new Error("stream closed without an end event"); // dropped: back off', "return;", VT + ["src/runs/useRunStream.test.tsx"]),
    ("M9 the log is not restarted on reconnect", S + "useRunStream.ts", "        setActivity([]);\n", "", VT + ["src/runs/useRunStream.test.tsx"]),
    ("M10 trace id taken from the whole traceparent", "apps/api/src/onb_api/runs/records.py", "return match.group(1)", "return str(traceparent)", PY + ["apps/api/tests/test_run_trace_id.py"]),
    ("M11 the all-zero trace id is accepted", "apps/api/src/onb_api/runs/records.py", 'or set(match.group(1)) == {"0"}', "", PY + ["apps/api/tests/test_run_trace_id.py"]),
    ("M12 a trace link for a malformed id", S + "policy.ts", "!TRACE_ID.test(traceId)", "false", VT + ["src/runs/policy.test.ts"]),
    ("M13 blocked run offers pause", S + "policy.ts", 'case "blocked":\n      return ["cancel"];', 'case "blocked":\n      return ["pause", "cancel"];', VT + ["src/runs/policy.test.ts"]),
]


def run(cmd: list[str]) -> tuple[int, str]:
    done = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False)
    out = (done.stdout + done.stderr).strip().splitlines()
    marks = [line.strip() for line in out if "✘" in line or "FAIL" in line or "failed" in line or "passed" in line]
    return done.returncode, " | ".join(marks[:3])


def main() -> int:
    lines: list[str] = []
    caught = 0
    for name, path, old, new, check in MUTATIONS:
        file = ROOT / path
        text = file.read_text()
        if text.count(old) != 1:
            lines.append(f"{name}: NOT APPLIED ({text.count(old)} matches in {path})")
            continue
        try:
            file.write_text(text.replace(old, new))
            code, marks = run(check)
        finally:
            subprocess.run(["git", "checkout", "--", path], cwd=ROOT, check=True)
        verdict = "caught" if code != 0 else "SURVIVED"
        caught += code != 0
        lines.append(f"{name}: {verdict} · {marks}")
        print(lines[-1], flush=True)
    lines.append(f"SUMMARY: {caught} of {len(MUTATIONS)} mutations caught")
    print(lines[-1])
    if "--write" in sys.argv:
        (ROOT / "docs/evidence/UI-5/mutation-checks.txt").write_text("\n".join(lines) + "\n")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
