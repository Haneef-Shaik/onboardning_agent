# Runbook: per-org key rotation and master key rotation

PRD §7.10 (Encryption, Integrity, Aadhaar), §7.9 Encryption, §15 "Key loss / leakage".
Code: `libs/py-crypto` (`onb_crypto`). Every step below is run, in order, by
`libs/py-crypto/tests/test_rotation_runbook.py` (in memory) and
`libs/py-crypto/tests_int/test_org_keys_mongo.py` (compose MongoDB). The step headings are checked by
`test_the_runbook_documents_exactly_the_exercised_steps`; keep them in sync.

## How the keys are laid out

- `PII_MASTER_KEY` (env only, 32 random bytes, base64) wraps every org key. It never encrypts data.
  The service refuses to start if it is missing, not base64 or not exactly 32 bytes
  (`MasterKey.from_env()` raises `MasterKeyError`).
- Collection `org_keys` (tenant collection, unique `{org_id, key_version}`) holds one document per
  org and key version. Each document holds five keys, each wrapped with AES-256-GCM under the master key
  and bound to `org_id | key_version | purpose`:
  `data` (vault values), `signing` (vault record `sig`), `index` (email/mobile/PAN blind indexes),
  `aadhaar` (the `pii_vault_aadhaar` partition) and `aadhaar_index` (`aadhaar_hash`).
  Documents also carry `master_key_id`, a public fingerprint of the master key that wrapped them.
- Unwrapped keys exist only in process memory (`Keyring` caches them per org).
- Ciphertext records the `key_version` it was written with. New writes use the newest version; old
  versions are kept forever so old ciphertext still decrypts.
- Rotation replaces `data`, `signing` and `aadhaar`. It carries `index` and `aadhaar_index` forward
  unchanged, because stored blind indexes must keep matching (re-indexing is not in the MVP).

Run the snippets from the repo root on the VM, through the Mongo SSH tunnel, with the service
environment loaded (`set -a && . ./.env && set +a`). `MONGO_URI` and `MONGO_DB` are the values the API
uses. Take a `mongodump --collection org_keys` before either procedure.

Shared setup for the snippets (`ops.py`, run with `uv run --frozen python ops.py`):

```python
import asyncio, os
from onb_data import connect
from onb_crypto import Keyring, MasterKey, RepositoryOrgKeyStore, rewrap_orgs, rotate_orgs, verify_orgs

async def main() -> None:
    store = connect(os.environ["MONGO_URI"], os.environ["MONGO_DB"])
    keyring = Keyring(MasterKey.from_env(), RepositoryOrgKeyStore(store.tenant("org_keys")))
    orgs = [o["_id"] for o in await store.shared("orgs").find({}, projection={"_id": 1})]
    ...  # the step's call goes here
    await store.close()

asyncio.run(main())
```

## Procedure A: rotate the org keys (scheduled, or one org's data key is suspected exposed)

No downtime. Rotate one org by passing `[org_id]` instead of `orgs`.

### A1. Record the active versions

`print(await verify_orgs(keyring, orgs))`. This unwraps every version of every org, so it also proves
the current master key opens everything. Any `KeyUnwrapError` → stop and investigate before rotating.

### A2. Rotate

`print(await rotate_orgs(keyring, orgs))`: each org gets version n+1. A `KeyVersionConflictError`
means another rotation of that org ran at the same time; run A1 again and rotate only the orgs that
did not move.

### A3. Restart the services that hold keys

API, orchestrator and workers cache unwrapped keys. Until they restart they keep writing with the
previous version, which is still valid; restarting makes them pick up the new one.

### A4. Verify

`print(await verify_orgs(keyring, orgs))` shows every org at n+1. Write and read one synthetic test
value per org: it must report the new `key_version`, and a value written before A2 must still read.

### A5. Keep the old versions

Never delete an old `org_keys` version: ciphertext written with it cannot be read without it.
Re-encrypting old records under the new version is not in the MVP.

## Procedure B: rotate the master key (scheduled, or `PII_MASTER_KEY` is suspected exposed)

Short write freeze. The data and the org keys do not change; only their wrapping does.

### B1. Generate the new master key

`openssl rand -base64 32`. Store it in the secret store the VM env is provisioned from, next to (not
over) the old one until B5.

### B2. Stop the writers

Stop the API, orchestrator and worker containers (`docker compose stop <service>…`), so nothing loads
keys while their wrapping changes.

### B3. Re-wrap every org's keys

With the **old** key in `PII_MASTER_KEY` and the new one in `NEW_PII_MASTER_KEY`:
`print(await rewrap_orgs(keyring, MasterKey.from_env({"PII_MASTER_KEY": os.environ["NEW_PII_MASTER_KEY"]}), orgs))`.
It is resumable: versions already wrapped by the new key are skipped, so a second run prints zeros.
A `RuntimeError` "changed while re-wrapping" means someone else touched that version: run B3 again.

### B4. Verify with the new master key only

Set `PII_MASTER_KEY` to the new key and run A1's `verify_orgs`. Every org must unwrap, at the same
active versions as before. Then start the services again.

### B5. Retire the old master key

The old key no longer opens anything (`verify_orgs` with it raises `KeyUnwrapError`). Keep it only as
long as the `org_keys` backup taken before B3 is kept, then destroy both.

## Key loss

If `PII_MASTER_KEY` is lost, every vault value of every org is unrecoverable: there is no escrow by
design. Keep the master key in the secret store with its own backup, never only in the VM env.
Production must move the master key to a KMS/HSM (PRD §7.10 Aadhaar row).
