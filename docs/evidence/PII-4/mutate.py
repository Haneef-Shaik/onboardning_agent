"""PII-4 mutation checks: break each guard, run the py-crypto unit tests, record which named tests fail,
restore the source.

Run from the repo root: `uv run --frozen python docs/evidence/PII-4/mutate.py`.
"""

import subprocess
import sys
from pathlib import Path

SRC = Path("libs/py-crypto/src/onb_crypto")

# (id, guard, file, original, mutant)
MUTATIONS = [
    (
        "G1",
        "box 3: subject binding, the vault no longer compares the record's subject with the grant's",
        "vault.py",
        "if record.subject_id != subject_id or record.field is not field:",
        "if record.field is not field:",
    ),
    (
        "G2",
        "box 3: subject binding, the sink resolves with the token's own subject instead of the grant's",
        "sink.py",
        "presented.token, org_id=grant.org_id, subject_id=grant.subject_id, field=presented.field",
        "presented.token, org_id=grant.org_id, field=presented.field, subject_id=(await self._store.tenant("
        '"pii_vault").find({"token": presented.token}, limit=1) or [{"subject": {"id": grant.subject_id}}])[0]'
        '["subject"]["id"]',
    ),
    (
        "G3",
        "box 1 bad signature: the Ed25519 signature is never checked",
        "grants.py",
        'if not key.verifies(_unb64(signature), f"{head}.{body}".encode("ascii")):',
        "if False:",
    ),
    (
        "G4",
        "box 1 bad signature: any header accepted once the kid is known",
        "grants.py",
        "if key is None or header != expected:",
        "if key is None:",
    ),
    ("G5", "box 1 expired: exp ignored", "grants.py", "if now >= claims.expires_at:", "if False:"),
    (
        "G6",
        "box 1 expired: a signed grant may live longer than 10 minutes",
        "grants.py",
        "if not timedelta(0) < expires_at - issued_at <= MAX_GRANT_TTL:",
        "if not timedelta(0) < expires_at - issued_at:",
    ),
    ("G7", "box 1 wrong sink: sink id not compared", "sink.py", "if grant.sink != sink_id:", "if False:"),
    ("G8", "box 1 another org: grant org not compared", "sink.py", "if grant.org_id != org_id:", "if False:"),
    (
        "G9",
        "box 1 field not granted: grant fields not checked",
        "sink.py",
        "if presented.field not in grant.fields:",
        "if False:",
    ),
    (
        "G10",
        "box 1 policy-forbidden field: the sink skips the field -> sink policy",
        "sink.py",
        "if not self._policy.allows(presented.field, sink_id):",
        "if False:",
    ),
    (
        "G11",
        "box 1 policy-forbidden field: the never-sinkable floor is dropped from the policy",
        "sink_policy.py",
        "forbidden = NEVER_SINKABLE & fields",
        "forbidden = frozenset[VaultField]()",
    ),
    ("G12", "box 1 replayed jti: jti never consumed", "sink.py", "await self._consume_jti(grant)", "pass"),
    (
        "G13",
        "box 1 replayed jti: a concurrent duplicate insert is not turned into a rejection",
        "sink.py",
        "except DuplicateKeyError:",
        "except KeyError:",
    ),
    ("G14", "sink: a purged token is released as '[purged]'", "sink.py", "if value == PURGED:", "if False:"),
    (
        "G15",
        "box 2: the access log stores the raw value instead of the mask",
        "sink.py",
        '"masked": _masked(presented.field, values[presented.token]),',
        '"masked": values[presented.token],',
    ),
    (
        "G16",
        "issuer: signs a field the policy does not allow at that sink",
        "grants.py",
        "refused = [f.value for f in granted if not self._policy.allows(f, sink)]",
        "refused: list[str] = []",
    ),
    (
        "G17",
        "issuer: lifetime not capped at 10 minutes",
        "grants.py",
        "if not timedelta(0) < ttl <= MAX_GRANT_TTL:",
        "if False:",
    ),
]


def run_tests() -> tuple[int, list[str]]:
    proc = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "libs/py-crypto/tests", "-q", "-p", "no:cacheprovider", "--no-header"],
        capture_output=True,
        text=True,
        check=False,
    )
    failed = [line for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
    return proc.returncode, [*failed, summary]


def main() -> int:
    survivors = []
    for mid, guard, name, original, mutant in MUTATIONS:
        path = SRC / name
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
    diff = subprocess.run(["git", "diff", "--stat", "--", str(SRC)], capture_output=True, text=True, check=False)
    print(f"source diff after all mutations: {diff.stdout.strip() or '0 files differ'}")
    print(f"survivors: {survivors or 'none'}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
