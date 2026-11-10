"""PII-1 mutation checks: break each guard, run the unit tests, record which named tests fail, restore.

Run from the repo root: `uv run --frozen python docs/evidence/PII-1/mutate.py`.
"""

import subprocess
import sys
from pathlib import Path

SRC = Path("libs/py-crypto/src/onb_crypto")

# (id, guard, file, original, mutant)
MUTATIONS = [
    (
        "M1",
        "box 1: per-org keys - the key cache leaks one org's keys to every org",
        "keyring.py",
        "cached = self._cache.get(org_id)",
        "cached = next(iter(self._cache.values()), None)",
    ),
    (
        "M2",
        "box 1: wrong master fails closed - unwrap swallows a bad tag and returns a random key",
        "envelope.py",
        "    except (InvalidTag, ValueError, TypeError):\n        raise KeyUnwrapError(",
        "    except (InvalidTag, ValueError, TypeError):\n        return SecretKey.generate()\n        raise KeyUnwrapError(",
    ),
    (
        "M3",
        "box 1: wrong master - the master key id check is removed (GCM tag must still fail)",
        "keyring.py",
        "if record.master_key_id != master.key_id:",
        "if False:",
    ),
    (
        "M4",
        "box 1: wrapped key not bound to its org",
        "envelope.py",
        "_SEP.join((_WRAP_LABEL, org_id.encode(), str(version).encode(), purpose.value.encode()))",
        "_SEP.join((_WRAP_LABEL, str(version).encode(), purpose.value.encode()))",
    ),
    (
        "M5",
        "box 1: store returns another org's record unchecked",
        "key_store.py",
        'if doc.get("org_id") != org_id:',
        "if False:",
    ),
    (
        "M6",
        "box 2: new writes keep using the oldest version after rotation",
        "keyring.py",
        "return (await self._load(_check_org(org_id)))[-1]",
        "return (await self._load(_check_org(org_id)))[0]",
    ),
    (
        "M7",
        "box 2: rotation drops old versions (old ciphertext must still decrypt)",
        "keyring.py",
        "unwrapped = tuple(self._unwrap(org_id, r) for r in records)",
        "unwrapped = tuple(self._unwrap(org_id, r) for r in records)[-1:]",
    ),
    (
        "M8",
        "box 2: rotation carries every key forward instead of replacing data/signing/aadhaar",
        "keys.py",
        "return self not in _INDEX_PURPOSES",
        "return False",
    ),
    (
        "M9",
        "box 3: key material logged on unwrap",
        "keyring.py",
        "        return OrgKeys(org_id, record.version, keys)",
        '        log.debug("unwrapped %s", keys[KeyPurpose.DATA].reveal().hex())\n'
        "        return OrgKeys(org_id, record.version, keys)",
    ),
    (
        "M10",
        "box 3: SecretKey repr shows its material",
        "keys.py",
        'return "SecretKey(<redacted>)"',
        'return f"SecretKey({self._material.hex()})"',
    ),
    (
        "M11",
        "fail-fast: a short master key is accepted (padded to 32 bytes)",
        "keys.py",
        "        if len(material) != KEY_BYTES:\n            raise MasterKeyError(",
        "        material = material.ljust(KEY_BYTES, b'\\0')[:KEY_BYTES]\n"
        "        if len(material) != KEY_BYTES:\n            raise MasterKeyError(",
    ),
    (
        "M12",
        "review H1: tenant context check removed (cached keys of another org served)",
        "keyring.py",
        "    if current_tenant().org_id != valid:",
        "    if False:",
    ),
    (
        "M13",
        "review H2: no reload on an unknown version (stale cache cannot read newer data)",
        "keyring.py",
        "        for attempt in range(2):",
        "        for attempt in range(1):",
    ),
    (
        "M14",
        "review M3: a load racing a rotation caches its stale snapshot",
        "keyring.py",
        "        if generation == self._generation:",
        "        if True:",
    ),
    (
        "M15",
        "review M4: duplicate key versions accepted",
        "keyring.py",
        "        if len(set(versions)) != len(versions):",
        "        if False:",
    ),
    (
        "M16",
        "review: rewrap does not re-check that no version is left on the old master",
        "keyring.py",
        "        if left:",
        "        if False:",
    ),
    (
        "M17",
        "A3: cached keys never expire",
        "keyring.py",
        "        if cached is not None and self._monotonic() - cached.loaded_at < self._cache_seconds:",
        "        if cached is not None:",
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
