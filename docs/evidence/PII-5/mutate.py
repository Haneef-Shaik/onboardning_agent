"""PII-5 mutation checks: break each guard, run the unit tests, record which named tests fail, restore.

Run from the repo root: `uv run --frozen python docs/evidence/PII-5/mutate.py`.
"""

import subprocess
import sys
from pathlib import Path

JOB = "apps/api/src/onb_api/retention/job.py"
LOOP = "apps/api/src/onb_api/retention/loop.py"
VAULT = "libs/py-crypto/src/onb_crypto/vault.py"
TESTS = ["apps/api/tests/test_retention.py", "apps/api/tests/test_retention_main.py", "libs/py-crypto/tests"]

# (id, guard, file, original, mutant)
MUTATIONS = [
    (
        "P1",
        "box 1: purge at exactly 90 days needs `>`, not `>=`",
        JOB,
        "if now - finished >= self._retention]",
        "if now - finished > self._retention]",
    ),
    (
        "P2",
        "box 1: purge two days early (day 89 must still resolve)",
        JOB,
        "if now - finished >= self._retention]",
        "if now - finished >= self._retention - timedelta(days=2)]",
    ),
    (
        "P3",
        "box 1: retention period of 30 days",
        JOB,
        "RETENTION_PERIOD: Final = timedelta(days=90)",
        "RETENTION_PERIOD: Final = timedelta(days=30)",
    ),
    (
        "P4",
        "box 1: clock starts at the last update, not the completion",
        JOB,
        '    for entry in reversed(list(run.get("history") or ())):',
        "    for entry in []:",
    ),
    (
        "P5",
        "box 1: a hire with a run still moving is purged",
        JOB,
        "if not await self._has_active_run(hire_id)]",
        "if True]",
    ),
    (
        "P6",
        "box 1: runs that did not complete start the clock",
        JOB,
        '{"status": RunState.COMPLETED.value, "dag_hash": {"$exists": True}},',
        '{"dag_hash": {"$exists": True}},',
    ),
    (
        "P7",
        "box 1: superseded versions keep their ciphertext",
        VAULT,
        '{"subject.type": SubjectType.NEW_HIRE.value, "subject.id": subject_id},\n                sort=',
        '{"subject.type": SubjectType.NEW_HIRE.value, "subject.id": subject_id, "status": "active"},\n                sort=',
    ),
    (
        "P8",
        "box 1: the Aadhaar partition is not purged",
        VAULT,
        "for collection in sorted({collection_for(field) for field in VaultField}):",
        'for collection in ["pii_vault"]:',
    ),
    (
        "P9",
        "box 2: the consent record is deleted with the values",
        VAULT,
        "        was_purged = record.status is VaultStatus.PURGED\n",
        "        was_purged = record.status is VaultStatus.PURGED\n"
        '        await self._store.tenant(CONSENT_COLLECTION).delete_one({"token": record.token})\n',
    ),
    (
        "P10",
        "box 3: purges are not audited",
        JOB,
        "                await self._audit_value(outcome)\n",
        "                pass\n",
    ),
    (
        "P11",
        "box 3: a purged hire is purged (and counted) again",
        JOB,
        "if hire is None or _PURGED_AT in hire:",
        "if hire is None:",
    ),
    (
        "P12",
        "box 3: a value already audited by a stopped pass is audited again",
        JOB,
        "if (await self._auditor.list(query, page=1, limit=1)).total:",
        "if False:",
    ),
    (
        "P13",
        "box 3: the hire is never marked purged",
        JOB,
        '**{f"pii_masked.{field}": PURGED for field in fields}, _PURGED_AT: self._clock()}',
        '**{f"pii_masked.{field}": PURGED for field in fields}}',
    ),
    (
        "P14",
        "hire document: masks keep a hint of the value",
        JOB,
        'f"pii_masked.{field}": PURGED for field in fields',
        'f"pii_masked.{field}": "masked" for field in fields',
    ),
    (
        "P15",
        "hire document: blind indexes stay",
        JOB,
        '("pii_versions", *_BLIND_INDEX_KEYS)',
        '("pii_versions",)',
    ),
    (
        "P16",
        "a failing hire stops the whole pass",
        JOB,
        "            return PurgeReport(failures=1)",
        "            raise",
    ),
    (
        "P17",
        "a failing hire is not audited",
        JOB,
        "            await self._audit_failure(hire_id, error)\n",
        "            pass\n",
    ),
    (
        "P18",
        "the loop dies on a failing pass",
        LOOP,
        "        except Exception as error:",
        "        except ZeroDivisionError as error:",
    ),
    (
        "P19",
        "the loop ignores the stop request",
        LOOP,
        "    while not stop.is_set():",
        "    while True:",
    ),
    (
        "P20",
        "a hire's purge ignores its org (every org's hire id is looked up)",
        JOB,
        'for org in await self._store.shared("orgs").find({}, projection={"_id": 1}):',
        'for org in (await self._store.shared("orgs").find({}, projection={"_id": 1}))[:1]:',
    ),
]


def run_tests() -> tuple[int, list[str]]:
    proc = subprocess.run(
        ["uv", "run", "--frozen", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "--no-header", "-x"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    failed = [line for line in proc.stdout.splitlines() if line.startswith("FAILED")]
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
    return proc.returncode, [*failed, summary]


def main() -> int:
    survivors = []
    for mid, guard, name, original, mutant in MUTATIONS:
        path = Path(name)
        text = path.read_text()
        if text.count(original) != 1:
            print(f"{mid}: pattern not found exactly once in {name}")
            return 2
        path.write_text(text.replace(original, mutant))
        try:
            code, lines = run_tests()
        finally:
            path.write_text(text)
        print(f"== {mid} ({guard}) -> {'KILLED' if code != 0 else 'SURVIVED'}")
        for line in lines:
            print(f"   {line}")
        if code == 0:
            survivors.append(mid)
    diff = subprocess.run(
        ["git", "diff", "--stat", "--", "apps/api/src", "libs/py-crypto/src"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"source diff after all mutations: {diff.stdout.strip() or '0 files differ'}")
    print(f"survivors: {survivors or 'none'}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
