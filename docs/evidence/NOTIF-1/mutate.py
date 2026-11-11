"""NOTIF-1 mutation checks: break each guard, run the tests that should catch it, record which named
tests failed, restore the source byte for byte.

Run from the repo root: `uv run --frozen python docs/evidence/NOTIF-1/mutate.py`.
"""

import subprocess
import sys
from pathlib import Path

SINK = "apps/mcp-mocks/src/onb_mcp_mocks/notify/sink.py"
SENDS = "apps/mcp-mocks/src/onb_mcp_mocks/notify/sends.py"
AUDIT = "apps/mcp-mocks/src/onb_mcp_mocks/notify/audit.py"
IN_APP = "apps/mcp-mocks/src/onb_mcp_mocks/notify/in_app.py"
SERVER = "apps/mcp-mocks/src/onb_mcp_mocks/notify/server.py"
API = "apps/api/src/onb_api/notify/service.py"
GATEWAY = "apps/mcp-hub/src/onb_mcp_hub/gateway.py"
DOWNSTREAM = "apps/mcp-hub/src/onb_mcp_hub/downstream.py"

MOCKS_TESTS = ["apps/mcp-mocks/tests"]
API_TESTS = ["apps/api/tests/test_notify_routes.py"]
HUB_TESTS = ["apps/mcp-hub/tests/test_notify.py"]

# (id, guard, file, original, mutant, tests)
MUTATIONS = [
    (
        "N1",
        "box 1: the outbox keeps the raw mobile instead of its mask",
        SINK,
        "recipient_masked=mask(MaskedField.MOBILE, mobile),",
        "recipient_masked=mobile,",
        MOCKS_TESTS,
    ),
    (
        "N2",
        "box 2: no guardrail decision for a refused grant",
        SINK,
        "await record_guardrail(self._auditor, caller, task_id, rejected.reason)",
        "pass",
        MOCKS_TESTS,
    ),
    (
        "N3",
        "box 2: the task is not failed (no task.sink_rejected event)",
        SINK,
        "await self._events.rejected(payload, run_id=caller.run_id)",
        "pass",
        MOCKS_TESTS,
    ),
    (
        "N4",
        "box 2: no grant audit record",
        AUDIT,
        "        await self._auditor.audit(\n            actor=Actor.agent(self._caller.agent),\n            action=GRANT_ACTION,",
        "        return\n        await self._auditor.audit(\n            actor=Actor.agent(self._caller.agent),\n            action=GRANT_ACTION,",
        MOCKS_TESTS,
    ),
    (
        "N5",
        "box 2: a refused grant still sends (the refusal is swallowed and the send goes on unresolved)",
        SINK,
        "            raise _refusal(rejected.reason) from None",
        "            values = {token: '9000000001'}",
        MOCKS_TESTS,
    ),
    (
        "N6",
        "replay: a used grant with other arguments counts as a replay of the first send",
        SENDS,
        '            if not same:\n                return Claim("conflict")',
        '            if False:\n                return Claim("conflict")',
        MOCKS_TESTS,
    ),
    (
        "N7",
        "jti sweep: deletes live rows too",
        SINK,
        'delete_many({"expires_at": {"$lt": cutoff}})',
        "delete_many({})",
        MOCKS_TESTS,
    ),
    (
        "N8",
        "body: a vault token in a telegram body is not refused",
        SINK,
        '        if _VAULT_REF_MARK in args.body:\n            raise SinkRefusedError("invalid_arguments", "invalid arguments: body (vault tokens are not allowed)")\n        token = args.recipient_token',
        "        token = args.recipient_token",
        MOCKS_TESTS,
    ),
    (
        "N9",
        "server: the call context may name another tool",
        SERVER,
        "if ctx.tool != self.name:",
        "if False:",
        MOCKS_TESTS,
    ),
    (
        "N10",
        "in-app: a redelivered job notifies again (no dedupe key)",
        IN_APP,
        'dedupe_key=f"{job.event_id}:{user_id}",',
        "dedupe_key=None,",
        MOCKS_TESTS,
    ),
    (
        "N11",
        "box 3: the webhook secret is not checked",
        API,
        "return presented is not None and hmac.compare_digest(presented.encode(), self._secret)",
        "return True",
        API_TESTS,
    ),
    (
        "N12",
        "webhook: a press decides in a fixed org instead of the button's",
        API,
        "        with tenant_scope(org_id):\n            await self._decide(query, button_id)",
        '        with tenant_scope("org_acme"):\n            await self._decide(query, button_id)',
        API_TESTS,
    ),
    (
        "N13",
        "webhook: the decision is taken from the request (always approve), not the stored button",
        API,
        '"decision": Decision(button["decision"]).value,',
        '"decision": "approve",',
        API_TESTS,
    ),
    (
        "N14",
        "outbox: a MANAGER sees the whole org's outbox",
        API,
        'query = {"audience_user_id": principal.user_id} if principal.role is Role.MANAGER else {}',
        "query: dict[str, str] = {}",
        API_TESTS,
    ),
    (
        "N15",
        "hub gateway: the grant is handed to every server",
        GATEWAY,
        "forwarded = pii_grant if tool.server in GRANT_SERVERS else None",
        "forwarded = pii_grant",
        HUB_TESTS,
    ),
    (
        "N16",
        "hub downstream: the grant header goes to every server",
        DOWNSTREAM,
        "if grant and tool.server in GRANT_SERVERS:",
        "if grant:",
        HUB_TESTS,
    ),
    (
        "N17",
        "hub gateway: a response echoing the grant is not withheld",
        GATEWAY,
        "held = (*self.secrets, *sent, *((pii_grant,) if pii_grant else ()))",
        "held = (*self.secrets, *sent)",
        HUB_TESTS,
    ),
    (
        "N18",
        "failure after resolution: not recorded, so a retry is resolved again (and replayed) instead of send_failed",
        SINK,
        "await self._sends.mark_failed(grant_digest)",
        "pass",
        MOCKS_TESTS,
    ),
    (
        "N19",
        "the grant's run is not compared with the call context's",
        SINK,
        "if verified.run_id != caller.run_id:",
        "if False:",
        MOCKS_TESTS,
    ),
    (
        "N20",
        "another org's verified grant has its run and task written into the caller's org",
        SINK,
        "return verified if verified.org_id == caller.org_id else None",
        "return verified",
        MOCKS_TESTS,
    ),
]


def run_tests(paths: list[str]) -> tuple[int, list[str]]:
    proc = subprocess.run(
        ["uv", "run", "--frozen", "pytest", *paths, "-q", "-p", "no:cacheprovider", "-o", "addopts="],
        capture_output=True,
        text=True,
        check=False,
    )
    failed = [line for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
    return proc.returncode, [*failed[:6], *(["..."] if len(failed) > 6 else []), summary]


def main() -> int:
    survivors = []
    touched = sorted({m[2] for m in MUTATIONS})
    for mid, guard, name, original, mutant, tests in MUTATIONS:
        path = Path(name)
        text = path.read_text()
        if text.count(original) != 1:
            print(f"{mid}: pattern not found exactly once in {name}")
            return 2
        path.write_text(text.replace(original, mutant))
        try:
            code, lines = run_tests(tests)
        finally:
            path.write_text(text)
        print(f"== {mid} ({guard}) -> {'KILLED' if code != 0 else 'SURVIVED'}")
        for line in lines:
            print(f"   {line}")
        if code == 0:
            survivors.append(mid)
    diff = subprocess.run(["git", "diff", "--stat", "--", *touched], capture_output=True, text=True, check=False)
    print(f"source diff after all mutations: {diff.stdout.strip() or '0 files differ'}")
    print(f"{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} killed; survivors: {survivors or 'none'}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
