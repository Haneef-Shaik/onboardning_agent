"""SEED-1 mutation checks: break each guard, run the named tests, record which failed, restore."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SEEDER = "apps/api/src/onb_api/seed/seeder.py"
SPEC = "apps/api/src/onb_api/seed/spec.py"
MAIN = "apps/api/src/onb_api/seed/__main__.py"
UNIT = ["apps/api/tests/test_seeder.py", "apps/api/tests/test_seed_spec.py", "apps/api/tests/test_seed_command.py"]
INT = ["apps/api/tests_int/test_mongo_seed.py"]

MUTATIONS = [
    ("M1", "org: no look before the insert", SEEDER,
     'if await self._s.orgs.find_one({"_id": plan.org.id}) is not None:', "if False:", UNIT),
    ("M2", "org: a duplicate insert is not taken as the same end state", SEEDER,
     "except DuplicateKeyError:  # another seed", "except LookupError:  # another seed", UNIT),
    ("M3", "keys: always create, never verify", SEEDER,
     "version = await self._s.keyring.verify(org_id)", "raise NoOrgKeysError(org_id)", UNIT),
    ("M4", "keys: any crypto error (a wrong master key) creates keys", SEEDER,
     "except NoOrgKeysError:\n            version = await self._s.keyring.create",
     "except Exception:\n            version = await self._s.keyring.create", UNIT),
    ("M5", "users: no look for an existing login", SEEDER,
     "if entry is not None:\n            if (entry.org_id", "if False:\n            if (entry.org_id", UNIT),
    ("M6", "users: an email registered to another user is accepted", SEEDER,
     "if (entry.org_id, entry.user_id) != (org_id, user.id):", "if False:", UNIT),
    ("M7", "users: a login entry without its record is accepted", SEEDER,
     "if await self._s.users.get_user(user.id) is None:", "if False:", UNIT),
    ("M8", "users: the password is stored instead of its hash", SEEDER,
     "await asyncio.to_thread(hash_password, user.password, rounds=self._rounds)", "user.password", UNIT),
    ("M9", "publisher: a stored non-HR_ADMIN may publish", SEEDER,
     "if stored is None or stored.role is not Role.HR_ADMIN:", "if stored is None:", UNIT),
    ("M10", "templates: no look for an existing version", SEEDER,
     "if current is not None:\n            return SeedStep", "if False:\n            return SeedStep", UNIT + INT),
    ("M11", "templates: a pending seed draft is not reused", SEEDER,
     "return next((d for d in drafts if d.status == \"draft\" and _body(d.template) == wanted), None)",
     "return None", UNIT),
    ("M12", "templates: a published draft counts as pending", SEEDER,
     "d.status == \"draft\" and _body", "_body", UNIT),
    ("M13", "spec: a password field in the seed file is ignored", SPEC,
     'extra="forbid", str_strip_whitespace', 'extra="ignore", str_strip_whitespace', UNIT),
    ("M14", "spec: any variable may hold a seed password", SPEC,
     "password_env: str = Field(pattern=_ENV_NAME)", "password_env: str = Field()", UNIT),
    ("M15", "spec: no minimum password length", SPEC,
     "if len(value) < MIN_PASSWORD_LENGTH:", "if False:", UNIT),
    ("M16", "spec: no bcrypt byte limit", SPEC,
     "if len(value.encode()) > MAX_PASSWORD_BYTES:", "if False:", UNIT),
    ("M17", "spec: a missing role is accepted", SPEC,
     "    if missing:\n        raise SeedConfigError(f\"the seed needs", "    if False:\n        raise SeedConfigError(f\"the seed needs", UNIT),
    ("M18", "spec: duplicate emails are accepted", SPEC,
     '_check_unique("user email", [normalise_email(u.email) for u in users])', "pass", UNIT),
    ("M19", "spec: an unset variable reads as an empty password", SPEC,
     'if not value:\n        raise SeedConfigError(f"{name} is not set")', "if False:\n        pass", UNIT),
    ("M20", "command: a database error prints its text (host, URI)", MAIN,
     'sys.stderr.write(f"seed failed: MongoDB error {type(error).__name__}\\n")',
     'sys.stderr.write(f"seed failed: MongoDB error {type(error).__name__}: {error}\\n")', UNIT),
    ("M21", "command: the database is reached before the settings are checked", MAIN,
     "    plan = load_seed(args.seed, env)\n    master_key = MasterKey.from_env(env)\n    uri, database = require_env(env, \"MONGO_URI\"), require_env(env, \"MONGO_DATABASE\")\n    store = connect(uri, database, app_name=\"seed\")",
     "    store = connect(env.get(\"MONGO_URI\", \"\") or \"mongodb://x\", env.get(\"MONGO_DATABASE\", \"\") or \"x\", app_name=\"seed\")\n    plan = load_seed(args.seed, env)\n    master_key = MasterKey.from_env(env)\n    uri, database = require_env(env, \"MONGO_URI\"), require_env(env, \"MONGO_DATABASE\")",
     UNIT),
]


def run(tests: list[str], env_file: bool) -> tuple[int, list[str]]:
    cmd = ["uv", "run", "--frozen", "pytest", *tests, "-q", "-p", "no:cacheprovider"]
    if env_file:
        cmd = ["bash", "-c", "set -a && . ./.env && set +a && exec \"$@\"", "withenv", *cmd]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    failed = [line for line in proc.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    return proc.returncode, failed


def main() -> int:
    only = set(sys.argv[1:])
    survived = []
    for mid, what, rel, old, new, tests in MUTATIONS:
        if only and mid not in only:
            continue
        path = ROOT / rel
        original = path.read_text()
        assert original.count(old) == 1, f"{mid}: pattern found {original.count(old)} times"
        path.write_text(original.replace(old, new))
        try:
            results = []
            unit = [t for t in tests if "tests_int" not in t]
            integ = [t for t in tests if "tests_int" in t]
            if unit:
                results.append(run(unit, env_file=False))
            if integ:
                results.append(run(integ, env_file=True))
        finally:
            path.write_text(original)
        failed = [f for _, fs in results for f in fs]
        killed = any(rc != 0 for rc, _ in results)
        print(f"{mid} {'KILLED' if killed else 'SURVIVED'} · {what} · {rel}")
        for f in failed:
            print(f"    {f}")
        if not killed:
            survived.append(mid)
    print(f"{len(MUTATIONS if not only else only) - len(survived)}/{len(MUTATIONS if not only else only)} killed; survived: {survived}")
    return 0 if not survived else 1


if __name__ == "__main__":
    sys.exit(main())
