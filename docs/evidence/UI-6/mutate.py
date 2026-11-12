"""UI-6 mutation checks: break each guard on purpose, run the test that should catch it, restore the file.

Run from the repo root: `uv run --frozen python docs/evidence/UI-6/mutate.py [M1 M2 ...]`. Prints one line per
mutation (KILLED / SURVIVED, with the failing test lines) and checks every file is restored at the end.
The Playwright mutations (E*) need free E2E ports: set E2E_API_PORT / E2E_WEB_PORT.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVICE = "apps/api/src/onb_api/notify/service.py"
ROUTES = "apps/api/src/onb_api/notify/routes.py"
RELAY = "apps/api/src/onb_api/notify/relay.py"
OUTBOX = "apps/web/src/approvals/MockOutbox.tsx"
INBOX = "apps/web/src/approvals/ApprovalsInbox.tsx"
API_TS = "apps/web/src/approvals/approvals-api.ts"
PRESS_TESTS = ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:randomly", "apps/api/tests/test_outbox_press.py"]
VITEST = ["node_modules/.bin/vitest", "run", "--config", "apps/web/vitest.config.ts", "apps/web/src/approvals"]
E2E = ["node_modules/.bin/playwright", "test", "--config", "apps/web/playwright.config.ts", "approvals"]


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
        "press: only the message's audience may press",
        SERVICE,
        'if message is None or message.get("audience_user_id") != principal.user_id:',
        "if message is None:",
        PRESS_TESTS,
    ),
    Mutation(
        "M2",
        "press: the button must be on that message",
        SERVICE,
        'if not any(b.get("callback_data") == callback_data for b in message.get("buttons") or []):',
        "if False:",
        PRESS_TESTS,
    ),
    Mutation(
        "M3",
        "press: an approval no longer pending is 409",
        SERVICE,
        'if approval is not None and approval.get("status") != "pending":',
        "if False:",
        PRESS_TESTS,
    ),
    Mutation(
        "M4",
        "press: goes through POST /webhooks/telegram (not straight to the handler)",
        ROUTES,
        """        await post_json(
            request.app,""",
        """        await channels.handle(TelegramUpdate.model_validate(update))
        await _noop(
            request.app,""",
        PRESS_TESTS,
    ),
    Mutation(
        "M5",
        "relay: the webhook request runs in an empty context",
        RELAY,
        "create_task(_call(app, scope, body), context=contextvars.Context())",
        "create_task(_call(app, scope, body))",
        PRESS_TESTS,
    ),
    Mutation(
        "M6",
        "relay: a non-2xx webhook answer is an error",
        RELAY,
        "if not 200 <= status < 300:",
        "if False:",
        PRESS_TESTS,
    ),
    Mutation(
        "M7",
        "press: the browser-facing route never sends the webhook secret",
        ROUTES,
        "    return respond(WebhookAck())\n\n\ndef telegram_sender(",
        "    raise AppError(ErrorCode.CONFLICT, channels.webhook_headers()[TELEGRAM_AUTH_HEADER])\n\n\n"
        "def telegram_sender(",
        PRESS_TESTS,
    ),
    Mutation(
        "M8",
        "press: one callback query per (message, button), so repeats publish once (security review)",
        SERVICE,
        '"id": _query_id(outbox_id, callback_data),',
        '"id": str(secrets.randbits(63)),',
        PRESS_TESTS,
    ),
    Mutation(
        "M9",
        "relay: a route that raises is a RelayError (503), not a 500 (code/security review)",
        RELAY,
        "    except Exception as error:  # the route crashed",
        "    except ZeroDivisionError as error:  # the route crashed",
        PRESS_TESTS,
    ),
    Mutation(
        "W1",
        "UI: buttons only on messages addressed to the signed-in user",
        OUTBOX,
        "const mine = message.audience_user_id === me.user_id;",
        "const mine = true;",
        VITEST,
    ),
    Mutation(
        "W2",
        'UI: a 409 decision shows "already decided"',
        INBOX,
        "isAlreadyDecided(decide.error) ? (",
        "false ? (",
        VITEST,
    ),
    Mutation(
        "W3",
        "UI: a press sends the pressed button's id",
        OUTBOX,
        "onClick={() => press.mutate(button.callback_data)}",
        "onClick={() => press.mutate(message.buttons[0]?.callback_data ?? '')}",
        VITEST,
    ),
    Mutation(
        "W4",
        "UI: the inbox lists the caller's own approvals",
        API_TS,
        "query: { mine: true }",
        "query: { mine: false }",
        VITEST,
    ),
    Mutation(
        "W5",
        'UI: a 409 press shows "already decided"',
        OUTBOX,
        "isAlreadyDecided(press.error) ? (",
        "false ? (",
        VITEST,
    ),
    Mutation(
        "E1",
        "e2e box 2: the outbox press reaches the webhook (handler called directly instead)",
        ROUTES,
        """        await post_json(
            request.app,""",
        """        await channels.handle(TelegramUpdate.model_validate(update))
        await _noop(
            request.app,""",
        E2E,
    ),
    Mutation(
        "E2",
        "e2e box 1: a web decision is recorded (the decision call is dropped)",
        INBOX,
        "mutationFn: (decision: Decision) => decideApproval(client, approval.id, decision, comment),",
        "mutationFn: async (_decision: Decision) => undefined,",
        E2E,
    ),
    Mutation(
        "E3",
        'e2e box 3: the outbox press on a decided approval says "already decided"',
        SERVICE,
        'if approval is not None and approval.get("status") != "pending":',
        "if False:",
        E2E,
    ),
]

NOOP = '\n\nasync def _noop(*_args: object, **_kwargs: object) -> None:\n    """Mutation helper."""\n'


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def run(mutation: Mutation) -> str:
    target = ROOT / mutation.path
    original = target.read_text()
    if original.count(mutation.old) != 1:
        return f"{mutation.key} ERROR: the guard text is not found exactly once in {mutation.path}"
    mutated = original.replace(mutation.old, mutation.new)
    if "_noop(" in mutation.new:
        mutated += NOOP
    target.write_text(mutated)
    try:
        result = subprocess.run(mutation.command, cwd=ROOT, capture_output=True, text=True, timeout=600)
    finally:
        target.write_text(original)
    output = result.stdout + result.stderr
    failed = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith(("FAILED", "×", "✘", "FAIL "))
    ]
    verdict = "KILLED" if result.returncode != 0 else "SURVIVED"
    shown = "; ".join(failed[:4]) or output.strip().splitlines()[-1:]
    return f"{mutation.key} {verdict} · {mutation.guard} · {mutation.path}\n    {shown}"


def main() -> int:
    wanted = set(sys.argv[1:])
    paths = sorted({m.path for m in MUTATIONS})
    before = {p: digest(p) for p in paths}
    survivors = 0
    for mutation in MUTATIONS:
        if wanted and mutation.key not in wanted:
            continue
        line = run(mutation)
        survivors += "SURVIVED" in line or "ERROR" in line
        print(line, flush=True)
    changed = [p for p in paths if digest(p) != before[p]]
    print(f"source diff after all mutations: {len(changed)} files differ")
    return 1 if survivors or changed else 0


if __name__ == "__main__":
    sys.exit(main())
