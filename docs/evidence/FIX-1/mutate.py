"""FIX-1 mutation checks: break each guard on purpose, run the named test, record which test failed, restore.

Run from the repo root on a clean tree: `uv run python docs/evidence/FIX-1/mutate.py`. Each mutation is
an exact text replacement in one file; the file is restored with `git checkout -- <file>` afterwards.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str
    tests: str


CONSUMER = "apps/orchestrator/src/onb_orchestrator/consumer.py"
RUNNER = "apps/orchestrator/src/onb_orchestrator/runner.py"
SERVICE = "apps/api/src/onb_api/hires/service.py"
ROUTES = "apps/api/src/onb_api/templates/routes.py"
STORE = "apps/api/src/onb_api/templates/store.py"
RACE = "apps/orchestrator/tests/test_pause_resume_race.py"
CONTROL = "apps/orchestrator/tests/test_run_control.py"
REVEAL = "apps/api/tests/test_reveal_race.py"
PAGING = "apps/api/tests/test_template_drafts_paging.py"
INT = "apps/api/tests_int/test_mongo_templates.py::test_the_draft_list_pages_over_mongo_within_the_org"

MUTATIONS = [
    Mutation(
        "M1 any halt under a running run fails it (the old code)",
        CONSUMER,
        "        if isinstance(halted, TaskCancelledError):\n",
        "        if True:\n",
        RACE,
    ),
    Mutation(
        "M2 a completion that lost to a pause/resume pair raises again",
        CONSUMER,
        "        await driver.is_moot(run_id)\n",
        "        if not await driver.is_moot(run_id):\n            raise\n",
        RACE,
    ),
    Mutation(
        "M3 a cancelled task raises a plain RunHaltedError (the run is left running)",
        RUNNER,
        '            raise TaskCancelledError("the task was cancelled")',
        '            raise RunHaltedError("the task was cancelled")',
        f"{CONTROL}::test_a_task_cancelled_under_a_running_run_ends_the_run_instead_of_leaving_it_running",
    ),
    Mutation(
        "M4 a superseded token is a vault failure again (500)",
        SERVICE,
        "            except SupersededTokenError:\n",
        "            except ZeroDivisionError:\n",
        REVEAL,
    ),
    Mutation(
        "M5 a single read: no re-read of the hire's newer token",
        SERVICE,
        "_REVEAL_READS: Final = 3",
        "_REVEAL_READS: Final = 1",
        REVEAL,
    ),
    Mutation(
        "M6 the 409 is not audited",
        SERVICE,
        "        await self._audit(principal, \"pii.reveal\", target, AuditOutcome.FAILED, _CHANGED_MEANWHILE)\n",
        "",
        REVEAL,
    ),
    Mutation(
        "M7 the route ignores limit",
        ROUTES,
        "    drafts, total = await lifecycle.page_drafts(page=page, limit=limit)",
        "    drafts, total = await lifecycle.page_drafts(page=page, limit=MAX_DRAFT_LIMIT)",
        PAGING,
    ),
    Mutation(
        "M8 the Mongo store does not skip earlier pages",
        STORE,
        "sort=_DRAFT_ORDER, skip=(page - 1) * limit, limit=limit)",
        "sort=_DRAFT_ORDER, skip=0, limit=limit)",
        INT,
    ),
    Mutation(
        "M9 the Mongo store has no tie-breaker (pages can overlap)",
        STORE,
        '_DRAFT_ORDER: list[tuple[str, int]] = [("updated_at", -1), ("_id", 1)]',
        '_DRAFT_ORDER: list[tuple[str, int]] = [("updated_at", -1)]',
        INT,
    ),
]


def run(mutation: Mutation) -> str:
    path = ROOT / mutation.path
    source = path.read_text()
    if source.count(mutation.old) != 1:
        return f"{mutation.name}: NOT APPLIED (text not found exactly once)"
    path.write_text(source.replace(mutation.old, mutation.new))
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", *mutation.tests.split(), "-q", "-p", "no:randomly", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(["git", "checkout", "--", mutation.path], cwd=ROOT, check=True)
    failed = [line for line in result.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    summary = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
    verdict = "KILLED" if result.returncode != 0 and failed else "SURVIVED"
    lines = [f"{mutation.name} [{mutation.path}] -> {verdict}: {summary}", *[f"    {line}" for line in failed[:4]]]
    return "\n".join(lines)


def main() -> int:
    reports = [run(m) for m in MUTATIONS]
    print("\n".join(reports))
    return 0 if all("KILLED" in r.splitlines()[0] for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
