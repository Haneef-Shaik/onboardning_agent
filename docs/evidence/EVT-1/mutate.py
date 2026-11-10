"""EVT-1 mutation checks: break each guard on purpose, run the named tests, record which fail, restore.

Run from the repo root with the compose stack up and `.env` loaded (the integration tests need it):

    set -a && . ./.env && set +a && uv run --frozen python docs/evidence/EVT-1/mutate.py

Exit 0 only when every mutation is caught by at least one test and every file is restored
byte-for-byte.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUS = "libs/py-eventbus/src/onb_eventbus"
DATA = "libs/py-data/src/onb_data"
UNIT = "libs/py-eventbus/tests"
INT = "libs/py-eventbus/tests_int"


@dataclass(frozen=True)
class Mutation:
    key: str
    guard: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTATIONS = [
    Mutation(
        "M1",
        "a held job (running, lease live) is never claimable by a second worker",
        f"{BUS}/documents.py",
        '{"status": JobStatus.PENDING.value, "available_at": {"$lte": now}},',
        '{"available_at": {"$lte": now}},',
        (UNIT, INT),
    ),
    Mutation(
        "M2",
        "a running job whose lease lapsed is claimable again (crash recovery)",
        f"{BUS}/documents.py",
        '            {"status": JobStatus.RUNNING.value, "locked_until": {"$lte": now}},\n',
        "",
        (UNIT, INT),
    ),
    Mutation(
        "M3",
        "a failure on the last attempt dead-letters the job",
        f"{BUS}/worker.py",
        'if attempts >= job["max_attempts"]:',
        'if attempts > job["max_attempts"]:',
        (UNIT, INT),
    ),
    Mutation(
        "M4",
        "the heartbeat renews the lease while the handler runs",
        f"{BUS}/worker.py",
        "        while True:\n            await asyncio.sleep(self._settings.heartbeat_seconds)\n",
        "        while True:\n            await asyncio.sleep(3600)\n",
        (UNIT, INT),
    ),
    Mutation(
        "M5",
        "writes after the claim match the lease token",
        f"{DATA}/queue.py",
        'return {"_id": job_id, "lease_token": lease_token}',
        'return {"_id": job_id}',
        (UNIT, "libs/py-data/tests", "libs/py-data/tests_int"),
    ),
    Mutation(
        "M6",
        "a repeated idempotency key writes nothing",
        f"{BUS}/bus.py",
        "        if existing is not None:\n            return PublishResult(event_id=existing[\"_id\"], duplicate=True)\n",
        "",
        (UNIT, INT),
    ),
    Mutation(
        "M7",
        "repository calls inside run_transaction join the transaction",
        f"{DATA}/session.py",
        'return {} if session is None else {"session": session}',
        "return {}",
        (UNIT, INT, "libs/py-data/tests", "libs/py-data/tests_int"),
    ),
    Mutation(
        "M8",
        "the consumer span continues the job's stored traceparent",
        f"{BUS}/worker.py",
        'job.get("traceparent"), f"consume {context.consumer}"',
        'None, f"consume {context.consumer}"',
        (UNIT, INT),
    ),
    Mutation(
        "M9",
        "a job without a valid org_id is dead-lettered before any handler runs",
        f"{BUS}/worker.py",
        "        org_problem = _org_problem(job)\n",
        "        org_problem = None\n",
        (UNIT, INT),
    ),
    Mutation(
        "M10",
        "the consumer runs in the tenant context of its job",
        f"{BUS}/worker.py",
        "            tenant_scope(context.org_id),\n",
        "            contextlib.nullcontext(),\n",
        (UNIT, INT),
    ),
    Mutation(
        "M11",
        "publish writes the change, event and jobs in one transaction",
        f"{BUS}/bus.py",
        "return await self._store.run_transaction(\n"
        "                    lambda: self._write(event_type, payload, idempotency_key, run_id, traceparent, change)\n"
        "                )",
        "return await self._write(event_type, payload, idempotency_key, run_id, traceparent, change)",
        (UNIT, INT),
    ),
    Mutation(
        "M12",
        "a claim filters on queue state only (cannot be aimed at an org or search payloads)",
        f"{DATA}/queue.py",
        "            elif key not in CLAIM_FILTER_FIELDS:\n",
        "            elif False:\n",
        ("libs/py-data/tests",),
    ),
    Mutation(
        "M13",
        "worker writes change queue state only (never payload or consumer)",
        f"{DATA}/queue.py",
        "        if allowed is None or not set(fields) <= allowed:\n",
        "        if False:\n",
        ("libs/py-data/tests",),
    ),
    Mutation(
        "M14",
        "jobs cannot be inserted or searched through the cross-org queue",
        f"{DATA}/queue.py",
        "        if self.collection_name != DEAD_LETTERS:\n",
        "        if False:\n",
        ("libs/py-data/tests",),
    ),
    Mutation(
        "M15",
        "a task started inside a transaction cannot use its session",
        f"{DATA}/session.py",
        "    if owner is not asyncio.current_task():\n",
        "    if False:\n",
        ("libs/py-data/tests",),
    ),
    Mutation(
        "M16",
        "a job with a corrupt attempt count is never claimed (no queue blockage)",
        f"{BUS}/documents.py",
        '        "attempts": {"$type": "number"},\n',
        "",
        (UNIT, INT),
    ),
    Mutation(
        "M17",
        "a heartbeat that keeps failing gives the job up once the lease could have lapsed",
        f"{BUS}/worker.py",
        "                held = now < held_until\n",
        "                held = True\n",
        (UNIT,),
    ),
    Mutation(
        "M18",
        "the publish span never records the exception message",
        f"{BUS}/bus.py",
        "            record_exception=False,\n",
        "            record_exception=True,\n",
        (UNIT,),
    ),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tests(paths: tuple[str, ...]) -> tuple[int, list[str]]:
    failed: list[str] = []
    worst = 0
    for target in paths:
        proc = subprocess.run(
            ["uv", "run", "--frozen", "pytest", target, "-q", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        worst = max(worst, proc.returncode)
        failed += [line for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    return worst, failed


def main() -> int:
    caught_all = True
    for m in MUTATIONS:
        path = ROOT / m.path
        original = path.read_bytes()
        before = digest(path)
        text = original.decode()
        if text.count(m.old) != 1:
            print(f"{m.key}: SETUP ERROR, the text to mutate occurs {text.count(m.old)} times in {m.path}")
            return 2
        path.write_text(text.replace(m.old, m.new))
        try:
            code, failed = run_tests(m.tests)
        finally:
            path.write_bytes(original)
        restored = digest(path) == before
        caught = code != 0 and bool(failed)
        caught_all = caught_all and caught and restored
        print(f"{m.key} [{'CAUGHT' if caught else 'MISSED'}] {m.guard}")
        print(f"   mutated {m.path}: {m.old.strip()[:70]!r} -> {m.new.strip()[:70]!r}")
        for line in failed:
            print(f"   {line}")
        print(f"   restored byte-for-byte: {restored}")
    print("ALL CAUGHT AND RESTORED" if caught_all else "SOME MUTATION MISSED OR NOT RESTORED")
    return 0 if caught_all else 1


if __name__ == "__main__":
    sys.exit(main())
