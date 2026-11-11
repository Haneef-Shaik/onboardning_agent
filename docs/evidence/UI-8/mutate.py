"""UI-8 mutation checks: break each guard on purpose, run its tests, record which test failed, restore.

Run from the repository root: `uv run --frozen python docs/evidence/UI-8/mutate.py`. Every file is
restored byte for byte, whatever happens. Output goes to stdout (saved as mutation-checks.txt).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTEST = ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf"]
VITEST = ["node_modules/.bin/vitest", "run", "--config", "apps/web/vitest.config.ts"]
PLAYWRIGHT = ["node_modules/.bin/playwright", "test", "--config", "apps/web/playwright.config.ts", "apps/web/e2e/admin.spec.ts"]


@dataclass(frozen=True)
class Mutation:
    key: str
    guard: str
    path: str
    old: str
    new: str
    command: list[str]


MUTATIONS = [
    Mutation(
        "M1",
        "eval routes are HR_ADMIN only",
        "apps/api/src/onb_api/evals/routes.py",
        "Depends(require_role(Role.HR_ADMIN))",
        "Depends(require_role(Role.HR_ADMIN, Role.HR))",
        [*PYTEST, "apps/api/tests/test_evals_route.py", "apps/api/tests/test_role_matrix.py"],
    ),
    Mutation(
        "M2",
        "another org's run is 404 like a missing one",
        "apps/api/src/onb_api/evals/routes.py",
        "        raise AppError(ErrorCode.NOT_FOUND)",
        "        raise AppError(ErrorCode.FORBIDDEN)",
        [*PYTEST, "apps/api/tests/test_evals_route.py"],
    ),
    Mutation(
        "M3",
        "the list honours the agent filter",
        "apps/api/src/onb_api/evals/store.py",
        'return {} if self.agent is None else {"agent": self.agent}',
        "return {}",
        [*PYTEST, "apps/api/tests/test_evals_route.py"],
    ),
    Mutation(
        "M4",
        "runs come newest first",
        "apps/api/src/onb_api/evals/store.py",
        '_NEWEST_FIRST: Final = [("started_at", -1), ("_id", -1)]',
        '_NEWEST_FIRST: Final = [("started_at", 1), ("_id", 1)]',
        [*PYTEST, "apps/api/tests/test_evals_route.py"],
    ),
    Mutation(
        "M5",
        "agent records carry the resolved connector and model",
        "apps/api/src/onb_api/agents/routes.py",
        "    if config is None:\n        return None",
        "    return None",
        [*PYTEST, "apps/api/tests/test_agents_route.py"],
    ),
    Mutation(
        "M6",
        "provider params never reach the response",
        "apps/api/src/onb_api/agents/routes.py",
        "        chosen = resolve_llm(config, manifest=selection).task_record()\n",
        "        resolved = resolve_llm(config, manifest=selection)\n"
        "        chosen = {**resolved.task_record(), 'model': f\"{resolved.model} {resolved.params}\"}\n",
        [*PYTEST, "apps/api/tests/test_agents_route.py"],
    ),
    Mutation(
        "M12",
        "one unresolvable manifest leaves that agent unresolved, not the list failed",
        "apps/api/src/onb_api/agents/routes.py",
        "    except LLMConfigError:\n",
        "    except ZeroDivisionError:\n",
        [*PYTEST, "apps/api/tests/test_agents_route.py"],
    ),
    Mutation(
        "M13",
        "apps never import the eval harness (the API tests write the stored shape themselves)",
        "apps/api/tests/test_role_matrix.py",
        "from onb_api.testing_evals import eval_report, store_eval_run\n",
        "from onb_api.testing_evals import eval_report, store_eval_run\nfrom onb_evals.results import record_run  # noqa: F401\n",
        [*PYTEST, "libs/py-evals/tests/test_harness_boundary.py"],
    ),
    Mutation(
        "M7",
        "recent denials ask the audit log for this agent's denied tool calls",
        "apps/web/src/admin/adminApi.ts",
        'export const DENIAL_FILTER = { action: "mcp.tool_call", outcome: "denied" } as const;',
        'export const DENIAL_FILTER = { action: "mcp.tool_call" } as const;',
        VITEST,
    ),
    Mutation(
        "M8",
        "the admin section sits behind the nav-table guard",
        "apps/web/src/App.tsx",
        "return canAccess(me.role, pathname) ? <>{children}</> : <NotAvailablePage />;",
        "return <>{children}</>;",
        VITEST,
    ),
    Mutation(
        "M9",
        "the PII access log never renders the vault token",
        "apps/web/src/admin/PiiAccessPage.tsx",
        "                  <Cell>{entry.reason ?? entry.sink ?? \"—\"}</Cell>",
        "                  <Cell>{entry.reason ?? entry.sink ?? \"—\"} {entry.token}</Cell>",
        VITEST,
    ),
    Mutation(
        "M10",
        "an agent's page lists its recent denials (Playwright)",
        "apps/web/src/admin/AgentPage.tsx",
        "            <DenialsCard name={agent.name} />\n",
        "",
        PLAYWRIGHT,
    ),
    Mutation(
        "M11",
        "the agent page shows the resolved model, not the alias (Playwright)",
        "apps/web/src/admin/AgentsPage.tsx",
        "  if (resolved) return { connector: resolved.connector, model: resolved.model };\n",
        "",
        PLAYWRIGHT,
    ),
]

FAIL_LINE = re.compile(r"(FAILED .*|.*(?:FAIL|✘|×) .*›.*|\s+✘.*|.* FAIL .*)")


def run(mutation: Mutation) -> tuple[bool, list[str]]:
    env = {**os.environ, "E2E_API_PORT": "19541", "E2E_WEB_PORT": "19542", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    result = subprocess.run(mutation.command, cwd=ROOT, capture_output=True, text=True, env=env, timeout=600)
    output = result.stdout + result.stderr
    failed = sorted({line.strip() for line in output.splitlines() if FAIL_LINE.fullmatch(line)})
    return result.returncode != 0, failed[:6]


def main() -> int:
    caught = 0
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        text = original.decode()
        if text.count(mutation.old) != 1:
            print(f"{mutation.key} SETUP ERROR: target not found once in {mutation.path}")
            return 2
        try:
            path.write_text(text.replace(mutation.old, mutation.new))
            failed, lines = run(mutation)
        finally:
            path.write_bytes(original)
        caught += failed
        print(f"{mutation.key} {'CAUGHT' if failed else 'SURVIVED'} · {mutation.guard} · {mutation.path}")
        for line in lines:
            print(f"    {line}")
    print(f"SUMMARY: {caught} of {len(MUTATIONS)} mutations caught")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
