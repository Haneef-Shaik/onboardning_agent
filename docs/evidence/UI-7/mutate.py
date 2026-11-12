"""Mutation checks for UI-7: break each guard on purpose, run the chat tests, record which test failed.

Run from the repo root on a committed tree (files are restored with `git checkout`):
    E2E_API_PORT=19410 E2E_WEB_PORT=19411 uv run --frozen python docs/evidence/UI-7/mutate.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CHAT = "apps/web/src/runs/chat"
PANEL = f"{CHAT}/ChatPanel.tsx"
MESSAGE = f"{CHAT}/MessageView.tsx"
MUTANTS = [
    (
        "M1 the retry is confirmed as soon as 'Review' is pressed (no dialog gate)",
        PANEL,
        "onReview={(m) => setReviewingId(m.id)} />",
        'onReview={(m) => confirm.mutate(m.proposal?.proposal_id ?? "")} />',
    ),
    (
        "M2 Cancel in the dialog confirms",
        PANEL,
        "onCancel={() => setReviewingId(null)}",
        "onCancel={() => confirm.mutate(reviewing.proposal_id)}",
    ),
    ("M3 a manager is offered the confirm button", PANEL, "const canConfirm = canControlRuns(role);", "const canConfirm = true;"),
    (
        "M4 a citation links to the wrong anchor",
        MESSAGE,
        "href={`#task-${citation.task_id}`}",
        "href={`#${citation.task_id}`}",
    ),
    (
        "M5 confirm sends another proposal id than the one shown",
        f"{CHAT}/chat-api.ts",
        "body: { proposal_id: proposalId }",
        'body: { proposal_id: "prop_000000000000000000000000" }',
    ),
    (
        "M6 an expired or used proposal still offers the button",
        MESSAGE,
        'proposal.status === "pending" && canConfirm',
        "canConfirm",
    ),
    (
        "M7 a successful confirm does not refresh the run page",
        PANEL,
        "onSuccess: () => onRunChanged(),",
        "onSuccess: () => undefined,",
    ),
]


def run(cmd: list[str]) -> tuple[int, str]:
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    return done.returncode, done.stdout + done.stderr


def tests() -> tuple[bool, str]:
    code, out = run(["npx", "vitest", "run", "--config", "apps/web/vitest.config.ts", "apps/web/src/runs"])
    if code != 0:
        failed = sorted(set(re.findall(r"FAIL\s+(\S.*)", out)))
        return False, "vitest: " + "; ".join(failed[:3])
    code, out = run(["npx", "playwright", "test", "--config", "apps/web/playwright.config.ts", "chat.spec.ts"])
    failed = re.findall(r"\d+\) \[chromium\] › (.*?) ──", out)
    return code == 0, "playwright: " + "; ".join(failed[:3])


def main() -> int:
    caught = 0
    for name, path, old, new in MUTANTS:
        target = Path(path)
        text = target.read_text()
        if old not in text:
            print(f"{name}: SKIPPED, pattern not found")
            continue
        target.write_text(text.replace(old, new, 1))
        try:
            survived, detail = tests()
        finally:
            run(["git", "checkout", "--", path])
        print(f"{name}: {'SURVIVED' if survived else 'caught'} — {detail}", flush=True)
        caught += 0 if survived else 1
    print(f"SUMMARY: {caught} of {len(MUTANTS)} mutations caught")
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
