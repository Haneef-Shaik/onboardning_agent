"""AUTH-1 mutation checks: break each guard on purpose, run the API tests, record which tests fail.

Run from the repo root on a clean tree: `uv run --frozen python docs/evidence/AUTH-1/mutate.py`.
Each mutation is one exact text replacement (it must match once); the file is restored from git after.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

API = "apps/api/src/onb_api"
MUTATIONS = [
    (
        "M1 token: claims no longer required",
        f"{API}/auth/tokens.py",
        'options={"require": list(_REQUIRED_CLAIMS)},',
        "options={},",
    ),
    (
        "M2 token: expiry not checked",
        f"{API}/auth/tokens.py",
        'options={"require": list(_REQUIRED_CLAIMS)},',
        'options={"require": list(_REQUIRED_CLAIMS), "verify_exp": False},',
    ),
    (
        "M3 token: signature not checked",
        f"{API}/auth/tokens.py",
        'options={"require": list(_REQUIRED_CLAIMS)},',
        'options={"require": list(_REQUIRED_CLAIMS), "verify_signature": False},',
    ),
    (
        # (Turning a missing header into an empty token is an equivalent mutation: read_token refuses
        # it too. This one lets the request through as a fixed principal.)
        "M4 dependency: missing token let through as a fixed principal",
        f"{API}/auth/dependencies.py",
        "    if credentials is None:\n        raise unauthenticated()\n",
        "    if credentials is None:\n"
        '        with tenant_scope("org_acme"):\n'
        '            yield Principal(user_id="u_acme_hr1", org_id="org_acme", role=Role.HR_ADMIN)\n'
        "        return\n",
    ),
    (
        "M5 dependency: route not run in the token's tenant scope",
        f"{API}/auth/dependencies.py",
        "    with tenant_scope(principal.org_id):\n        yield principal\n",
        "    yield principal\n",
    ),
    (
        "M6 require_role: role not checked",
        f"{API}/auth/dependencies.py",
        "        if principal.role not in allowed:",
        "        if False:",
    ),
    (
        "M7 manager scope: filter not narrowed",
        f"{API}/auth/scope.py",
        'return {"manager_id": principal.user_id} if principal.role is Role.MANAGER else {}',
        "return {}",
    ),
    (
        "M8 manager scope: single record not guarded",
        f"{API}/auth/scope.py",
        'if principal.role is Role.MANAGER and record.get("manager_id") != principal.user_id:',
        "if False:",
    ),
    (
        "M9 settings: JWT_SECRET falls back to a default",
        f"{API}/auth/settings.py",
        'secret = require_env(source, "JWT_SECRET")',
        'secret = source.get("JWT_SECRET") or "fallback-secret-fallback-secret-xx"',
    ),
    (
        "M10 login: password not checked",
        f"{API}/auth/routes.py",
        "    if user is None or not password_ok:",
        "    if user is None:",
    ),
    (
        "M11 login body: unknown fields (org_id) ignored",
        "libs/py-contracts/src/onb_contracts/auth.py",
        "class LoginRequest(RequestModel):\n",
        'class LoginRequest(RequestModel):\n    model_config = ConfigDict(frozen=True, extra="ignore")\n',
    ),
    (
        "M12 a new route without a token guard or a role-matrix row",
        f"{API}/app.py",
        "    app.include_router(auth_routes.router)\n",
        '    app.include_router(auth_routes.router)\n    app.add_api_route("/unguarded", lambda: None)\n',
    ),
    (
        "M13 data errors: a foreign id answers 403 instead of 404",
        f"{API}/errors.py",
        "    NotFoundError: ErrorCode.NOT_FOUND,",
        "    NotFoundError: ErrorCode.FORBIDDEN,",
    ),
    (
        "M14 token: lifetime longer than configured accepted",
        f"{API}/auth/tokens.py",
        "    if exp - iat > settings.token_ttl.total_seconds():",
        "    if False:",
    ),
    (
        "M15 token: audience not checked",
        f"{API}/auth/tokens.py",
        "            audience=AUDIENCE,\n",
        "            audience=None,\n",
    ),
    (
        "M16 dependency: tenant scope opened but never closed",
        f"{API}/auth/dependencies.py",
        "    with tenant_scope(principal.org_id):\n        yield principal\n",
        "    tenant_scope(principal.org_id).__enter__()\n    yield principal\n",
    ),
]

FAILED = re.compile(r"^FAILED (\S+)", re.MULTILINE)


def run_once(label: str, path: str, old: str, new: str) -> bool:
    file = pathlib.Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        print(f"{label}: SETUP ERROR, pattern found {text.count(old)} times")
        return False
    file.write_text(text.replace(old, new))
    try:
        result = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "apps/api/tests", "libs/py-contracts/tests", "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(["git", "checkout", "--", path], check=True)
    failed = FAILED.findall(result.stdout)
    print(f"{label}\n  {path}: {old.strip()!r} -> {new.strip()!r}")
    print(f"  caught: {'yes' if failed else 'NO'} ({len(failed)} failed)")
    for name in failed[:6]:
        print(f"    FAILED {name}")
    if len(failed) > 6:
        print(f"    ... and {len(failed) - 6} more")
    return bool(failed)


def main() -> int:
    caught = [run_once(*mutation) for mutation in MUTATIONS]
    print(f"\n{sum(caught)}/{len(caught)} mutations caught")
    return 0 if all(caught) else 1


if __name__ == "__main__":
    sys.exit(main())
