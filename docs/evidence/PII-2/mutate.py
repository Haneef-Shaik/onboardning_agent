"""PII-2 mutation checks: break each guard, run the unit tests, record which named tests fail, restore.

Run from the repo root: `uv run --frozen python docs/evidence/PII-2/mutate.py`.
"""

import subprocess
import sys
from pathlib import Path

SRC = Path("libs/py-crypto/src/onb_crypto")

# (id, guard, file, original, mutant)
MUTATIONS = [
    ("V1", "box 1: subject.id not signed", "vault_record.py", "        record.subject_id,\n", ""),
    (
        "V2",
        "box 1: ciphertext, nonce and key_version not signed",
        "vault_record.py",
        "        record.ciphertext,\n        record.nonce,\n        record.key_version,\n",
        "",
    ),
    (
        "V3",
        "box 1: field not signed",
        "vault_record.py",
        "        record.field.value,\n        record.ciphertext,",
        "        record.ciphertext,",
    ),
    ("V4", "box 1: status not signed", "vault_record.py", "        record.status.value,\n", ""),
    (
        "V5",
        "box 1: sig never compared",
        "vault.py",
        "if not hmac.compare_digest(record.sig, await self._signature(record)):",
        "if False:",
    ),
    (
        "V6",
        "box 1 (defence in depth): AAD binds only org and field",
        "vault.py",
        "return associated_data(org_id, subject_id, field.value, token, version)",
        "return associated_data(org_id, field.value)",
    ),
    (
        "V7",
        "box 2: the old record is never superseded",
        "vault.py",
        "if old.status is VaultStatus.ACTIVE and old.version < newest.version:",
        "if False:",
    ),
    (
        "V8",
        "box 2: an update does not get a new version",
        "vault.py",
        "version = 1 if current is None else current.version + 1",
        "version = 1",
    ),
    (
        "V9",
        "box 3: Aadhaar written to the general partition",
        "vault_record.py",
        "return AADHAAR_VAULT_COLLECTION if field is VaultField.AADHAAR else VAULT_COLLECTION",
        "return VAULT_COLLECTION",
    ),
    (
        "V10",
        "box 3: Aadhaar encrypted with the data key",
        "vault.py",
        "return KeyPurpose.AADHAAR if field is VaultField.AADHAAR else KeyPurpose.DATA",
        "return KeyPurpose.DATA",
    ),
    (
        "V11",
        "box 4: the 128 extra bits come from `random`, not `secrets`",
        "tokens.py",
        'extra = int.from_bytes(secrets.token_bytes(_EXTRA_RANDOM_BYTES), "big")',
        'extra = int.from_bytes(__import__("random").randbytes(_EXTRA_RANDOM_BYTES), "big")',
    ),
    (
        "V12",
        "box 4: token without the 128 extra bits (wrong length)",
        "tokens.py",
        'return f"{PTK_PREFIX}{_base32(ulid, _PART_CHARS)}{_base32(extra, _PART_CHARS)}"',
        'return f"{PTK_PREFIX}{_base32(ulid, _PART_CHARS)}"',
    ),
    (
        "V13",
        "box 3: a record in the wrong partition is accepted",
        "vault.py",
        "if collection_for(record.field) != collection:",
        "if False:",
    ),
    (
        "V14",
        "binding: the reader's expected subject and field are ignored",
        "vault.py",
        "if record.subject_id != subject_id or record.field is not field:",
        "if False:",
    ),
    (
        "V15",
        "box 2: a superseded token resolves by default",
        "vault.py",
        "if not allow_superseded and not await self._is_live(record):",
        "if False:",
    ),
    (
        "V16",
        "retention: a purged record may keep ciphertext",
        "vault_record.py",
        "if ciphertext or nonce:",
        "if False:",
    ),
    ("V17", "a token stored twice is accepted", "vault.py", "if len(docs) > 1:", "if False:"),
    (
        "V18",
        "box 2: an update builds on the current record without verifying it",
        "vault.py",
        "return await self._read(field, token, org_id)",
        "return record_from_document(docs[0])",
    ),
    (
        "V19",
        "Aadhaar stored without a consent record",
        "vault.py",
        "if field is VaultField.AADHAAR and not isinstance(consent, AadhaarConsent):",
        "if False:",
    ),
    (
        "V20",
        "review H1: an older record still marked active resolves (replayed or half-superseded)",
        "vault.py",
        "if newest is None or newest.token != record.token:",
        "if False:",
    ),
    (
        "V21",
        "review: consent record left behind when the vault insert fails",
        "vault.py",
        'await self._store.tenant(CONSENT_COLLECTION).delete_one({"token": record.token})',
        "pass",
    ),
    (
        "V22",
        "review: a supersede that matched nothing is accepted without re-reading",
        "vault.py",
        "if now.status is VaultStatus.ACTIVE:",
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
    failed = [line for line in proc.stdout.splitlines() if line.startswith("FAILED")]
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
