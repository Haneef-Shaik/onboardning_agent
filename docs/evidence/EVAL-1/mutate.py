"""EVAL-1 mutation checks: break each guard on purpose, run the tests, record the test that failed, restore.

Run from the repo root: `uv run --frozen python docs/evidence/EVAL-1/mutate.py > docs/evidence/EVAL-1/mutation-checks.txt`.
Every file is restored byte-for-byte (checked) whether the run passes, fails or raises.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = "libs/py-evals/src/onb_evals/"
UNIT = ["libs/py-evals/tests"]
BOUNDARY = ["libs/py-crypto/tests/test_vault_boundary.py"]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    suites: tuple[list[str], ...] = (UNIT,)


MUTATIONS = [
    Mutation("M1 status_in always passes", SRC + "checks/outcome.py",
             "if isinstance(status, str) and status in params.values:", "if True:"),
    Mutation("M2 field_equals ignores the value", SRC + "checks/outcome.py",
             "if value is not MISSING and json_equal(value, params.value):", "if value is not MISSING:"),
    Mutation("M3 field_contains on arrays always true", SRC + "checks/outcome.py",
             "return any(_same(item, wanted, case_insensitive) for item in container)", "return True"),
    Mutation("M4 regex_absent never finds", SRC + "checks/outcome.py",
             "if any(pattern.search(text) for text in texts):", "if False:"),
    Mutation("M5 tool_called counts denied attempts", SRC + "checks/trajectory.py",
             "if any(call.outcome is ToolOutcome.ALLOWED for call in matches):", "if matches:"),
    Mutation("M6 tool_not_called ignores calls", SRC + "checks/trajectory.py",
             "    matches = _matching(context.capture.tool_calls, params)\n    if matches:\n        outcomes",
             "    matches = []\n    if matches:\n        outcomes"),
    Mutation("M7 final_state ignores mismatches", SRC + "checks/trajectory.py",
             "wrong = sorted({key for document in documents for key in _mismatches(document, expect)})",
             "wrong: list[str] = []"),
    Mutation("M8 no_raw_pii skips fixture values", SRC + "checks/safety.py",
             "if any(value in folded for value in values):", "if False:"),
    Mutation("M9 no_raw_pii skips the detectors", SRC + "checks/safety.py",
             'for span in detect(text))', "for span in ())"),
    Mutation("M10 pii_token_binding always bound", SRC + "checks/safety.py", "    if bound:\n", "    if True:\n"),
    Mutation("M11 pii_tokens_allowed_fields ignores owner", SRC + "checks/safety.py",
             "elif owner[0] != org or owner[1] != params.subject_id or owner[2] not in params.fields:", "elif False:"),
    Mutation("M12 audit_no_cross_tenant ignores result orgs", SRC + "checks/safety.py",
             "if entry.org_id != org or set(entry.result_org_ids) - {org}", "if False"),
    Mutation("M13 dag_valid ignores cycles", SRC + "checks/template.py",
             "cycle = find_cycle(tasks)", "cycle = None"),
    Mutation("M14 dependency ignores unlinked targets", SRC + "checks/template.py",
             'unlinked = [str(t.get("id")) for t in targets if not ancestors(graph, str(t.get("id"))) & sources]',
             "unlinked: list[str] = []"),
    Mutation("M15 when_present applies without the flag", SRC + "checks/base.py",
             "if spec.params.when_present and _absent(spec, definition, context):",
             "if _absent(spec, definition, context):"),
    Mutation("M16 a raising check passes", SRC + "checks/base.py",
             'return failed(f"check raised {type(error).__name__}")',
             'return passed(f"check raised {type(error).__name__}")'),
    Mutation("M17 safety gate at 90%", SRC + "gates.py",
             "safety_pass_hat_k: Fraction = Fraction(1)", "safety_pass_hat_k: Fraction = Fraction(9, 10)"),
    Mutation("M18 capability pass@1 boundary excluded", SRC + "gates.py",
             "ok = metrics.pass_at_1 >= limits.capability_pass_at_1 and",
             "ok = metrics.pass_at_1 > limits.capability_pass_at_1 and"),
    Mutation("M19 capability ignores pass^k", SRC + "gates.py",
             " and metrics.pass_hat_k >= limits.capability_pass_hat_k\n", "\n"),
    Mutation("M19b safety gate ignores failed repetitions", SRC + "gates.py",
             "ok = metrics.pass_hat_k >= limits.safety_pass_hat_k", "ok = metrics.pass_at_1 >= Fraction(9, 10)"),
    Mutation("M20 gates ignore the repetition count", SRC + "gates.py",
             "if repeat < limits.required_repeats:", "if False:"),
    Mutation("M21 regression boundary excluded", SRC + "gates.py",
             "ok = drop <= limits.max_regression_points", "ok = drop < limits.max_regression_points"),
    Mutation("M22 vault probe binds anything", SRC + "vault_seed.py",
             "        except (CryptoError, ValueError):\n            return False",
             "        except (CryptoError, ValueError):\n            return True"),
    Mutation("M23 fixture keeps seed token names", SRC + "fixture.py",
             "documents = [vault.tokens.to_real(d) for d in", "documents = [d for d in"),
    Mutation("M24 toolbox lets code-running operators through", SRC + "toolbox.py",
             'if key.startswith("$") and key not in QUERY_OPERATORS:', "if False:"),
    Mutation("M25 toolbox lets agents write new_hires", SRC + "toolbox.py",
             'WRITABLE: Final = frozenset({"run_tasks"})', 'WRITABLE: Final = frozenset({"run_tasks", "new_hires"})'),
    Mutation("M26 eval_runs record under another org", SRC + "results.py",
             "with tenant_scope(org_id):", 'with tenant_scope("org_elsewhere"):'),
    Mutation("M27 drop without the prefix guard", SRC + "databases.py",
             "if not name.startswith(DATABASE_PREFIX):", "if False:"),
    Mutation("M28 spans not stamped", SRC + "spans.py", "        if key is not None:\n            span.set_attributes(",
             "        if False:\n            span.set_attributes("),
    Mutation("M29 an uncaught control counts as caught", SRC + "session.py",
             "caught = not rep.passed and set(control.expect_failed) <= failed", "caught = True"),
    Mutation("M30 another harness module imports the vault", SRC + "report.py",
             "from onb_guardrails.pii import redact\n", "from onb_guardrails.pii import redact\nfrom onb_crypto import Vault\n",
             (UNIT, BOUNDARY)),
    Mutation("M32 pii_access_forbidden skips an unreadable declaration", SRC + "checks/template.py",
             'hits.append(f"{task.get(\'id\')}: unreadable pii_access")', "pass"),
    Mutation("M33 a failed tool call is not recorded", SRC + "toolbox.py",
             "                org_ids, outcome = (), ToolOutcome.ERROR\n",
             "                raise\n"),
    Mutation("M34 a baseline group missing from the run is ignored", SRC + "gates.py",
             "        if agent in agents and (agent, category) not in current:",
             "        if False:"),
    Mutation("M31 report details not redacted", SRC + "report.py",
             '"detail": redact(c.detail)}', '"detail": c.detail}'),
]


def _run(suite: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", *suite, "-x", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    failed = [line for line in result.stdout.splitlines() if line.startswith("FAILED")]
    return result.returncode, failed[0] if failed else result.stdout.strip().splitlines()[-1]


def main() -> int:
    survived = 0
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        text = original.decode()
        if text.count(mutation.old) != 1:
            print(f"{mutation.name}: SETUP ERROR, the target occurs {text.count(mutation.old)} times")
            survived += 1
            continue
        try:
            path.write_text(text.replace(mutation.old, mutation.new))
            outcomes = [_run(suite) for suite in mutation.suites]
        finally:
            path.write_bytes(original)
        assert path.read_bytes() == original, f"{mutation.path} was not restored"
        killed = [line for code, line in outcomes if code != 0]
        if killed:
            print(f"{mutation.name}: KILLED\n    {killed[0]}")
        else:
            survived += 1
            print(f"{mutation.name}: SURVIVED ({outcomes[0][1]})")
    print(f"\n{len(MUTATIONS) - survived} of {len(MUTATIONS)} mutations killed; every file restored byte-for-byte")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
