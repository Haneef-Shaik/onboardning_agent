"""UI-4 mutation checks: break each named guard, run the tests that must catch it, restore the file.

Run from the repository root (needs `.env` for nothing; the API tests use the in-memory database):
    uv run --frozen python docs/evidence/UI-4/mutate.py --write
Every file is restored with `git checkout -- <file>` (the tree must be clean when this starts).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VITEST = ["npx", "vitest", "run", "--config", "apps/web/vitest.config.ts"]
PYTEST = ["uv", "run", "--frozen", "pytest", "-q", "-x"]


@dataclass(frozen=True)
class Mutant:
    key: str
    guard: str
    file: str
    old: str
    new: str
    cmd: list[str]
    expect: str  # a test name that must be among the failures


MUTANTS = [
    Mutant("M1", "HR may reveal Aadhaar (UI)", "apps/web/src/hires/policy.ts",
           'if (role === "HR") return field !== "aadhaar";', 'if (role === "HR") return true;',
           [*VITEST, "src/hires"], "lets HR reveal every field but Aadhaar"),
    Mutant("M2", "a manager may reveal (UI)", "apps/web/src/hires/policy.ts",
           'if (role === "HR") return field !== "aadhaar";\n  return false;',
           'if (role === "HR") return field !== "aadhaar";\n  return true;',
           [*VITEST, "src/hires"], "never lets a manager reveal anything"),
    Mutant("M3", "a revealed value never expires", "apps/web/src/hires/useReveal.ts",
           "setTimeout(() => hide(field), answer.expires_in_seconds * 1000)",
           "setTimeout(() => undefined, answer.expires_in_seconds * 1000)",
           [*VITEST, "src/hires"], "holds a revealed value, then drops it after the TTL the API names"),
    Mutant("M4", "the TTL ignores the API's seconds (never hides before 10 min)", "apps/web/src/hires/useReveal.ts",
           "answer.expires_in_seconds * 1000", "600_000",
           [*VITEST, "src/hires"], "holds a revealed value, then drops it after the TTL the API names"),
    Mutant("M5", "a reveal needs no reason", "apps/web/src/hires/HireDetail.tsx",
           'disabled={busy || reason.trim() === ""}', "disabled={busy}",
           [*VITEST, "src/hires"], "asks for a reason, reveals one field, and hides it again after the TTL"),
    Mutant("M6", "the upload sends no media type of its own", "apps/web/src/hires/hires-api.ts",
           'headers: { "Content-Type": file.type },', 'headers: { "Content-Type": "application/octet-stream" },',
           [*VITEST, "src/hires"], "uploads a scan with its type and media type, then lists its metadata"),
    Mutant("M7", "a non-scan file is sent anyway", "apps/web/src/hires/DocumentsPanel.tsx",
           "if (file === null || !isDocumentMediaType(file.type)) {", "if (file === null) {",
           [*VITEST, "src/hires"], "refuses a file that is not a PDF, JPEG or PNG before sending it"),
    Mutant("M8", "an Aadhaar goes without consent", "apps/web/src/hires/new-hire.ts",
           'if (v.aadhaar !== "" && v.aadhaarConsent === "") errors.aadhaarConsent = CONSENT_MESSAGE;', "",
           [*VITEST, "src/hires/new-hire"], "needs the hire's consent purpose whenever an Aadhaar is entered"),
    Mutant("M9", "unverified shows as verified", "apps/web/src/hires/DocumentsPanel.tsx",
           'doc.verified ? "Verified" : "Not verified"', '"Verified"',
           [*VITEST, "src/hires"], "lists existing scans with type, upload time and verified flag"),
    Mutant("M10", "the list route lets a manager in", "apps/api/src/onb_api/documents/routes.py",
           'async def list_hire_documents(_: Hr,', 'async def list_hire_documents(_: Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN, Role.MANAGER))],',
           [*PYTEST, "apps/api/tests/test_documents.py", "apps/api/tests/test_role_matrix.py"], "test_a_manager_cannot_list_scans"),
    Mutant("M11", "the list returns every hire's scans", "apps/api/src/onb_api/documents/meta.py",
           'find({"hire_id": hire_id},', "find({},",
           [*PYTEST, "apps/api/tests/test_documents.py"], "test_the_list_holds_only_that_hires_scans"),
    Mutant("M12", "the list does not check the hire is the caller org's", "apps/api/src/onb_api/documents/service.py",
           "        if await self._hires.get(hire_id) is None:\n            raise AppError(ErrorCode.NOT_FOUND)\n        return await self.meta.list_for_hire(hire_id)",
           "        return await self.meta.list_for_hire(hire_id)",
           [*PYTEST, "apps/api/tests/test_documents.py"], "test_listing_the_scans_of_a_missing_or_foreign_hire_is_404"),
    Mutant("M13", "the list is not oldest-first", "apps/api/src/onb_api/documents/meta.py",
           'sort=[("uploaded_at", 1), ("_id", 1)]', 'sort=[("uploaded_at", -1), ("_id", 1)]',
           [*PYTEST, "apps/api/tests/test_documents.py"], "test_hr_lists_a_hires_scans_as_metadata_only"),
    Mutant("M14", "a reveal that resolves after hide/clear/unmount still shows", "apps/web/src/hires/useReveal.ts",
           "if (tickets.current.get(field) !== ticket) return;", "if (false) return;",
           [*VITEST, "src/hires/useReveal"], "drops an answer that arrives after unmount, and leaves no timer behind"),
]


def run(cmd: list[str]) -> str:
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    return done.stdout + done.stderr


def main() -> int:
    lines: list[str] = []
    caught = 0
    for m in MUTANTS:
        path = ROOT / m.file
        source = path.read_text()
        assert source.count(m.old) == 1, f"{m.key}: pattern not unique in {m.file}"
        path.write_text(source.replace(m.old, m.new))
        try:
            output = run(m.cmd)
        finally:
            subprocess.run(["git", "checkout", "--", m.file], cwd=ROOT, check=True)
        failed_named = m.expect in output and ("FAIL" in output or "FAILED" in output)
        caught += failed_named
        lines.append(f"{m.key} {'CAUGHT' if failed_named else 'SURVIVED'} · {m.guard} · {m.file} · expected failure: {m.expect}")
    lines.append(f"SUMMARY: {caught} of {len(MUTANTS)} mutations caught")
    report = "\n".join(lines)
    print(report)
    if "--write" in sys.argv:
        (ROOT / "docs/evidence/UI-4/mutation-checks.txt").write_text(report + "\n")
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
