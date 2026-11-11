"""T-3 mutation checks: break one guard, run the cross-tenant suite, record which tests fail, restore."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "docs/evidence/T-3/mutations.txt"

MUTATIONS = [
    (
        "M1 TenantRepository.count drops the org filter (mcp-mongo count/find/aggregate totals)",
        "libs/py-data/src/onb_data/repository.py",
        "        return await self._collection.count_documents(self._filter(query), **session_kwargs())",
        "        return await self._collection.count_documents(dict(query or {}), **session_kwargs())",
    ),
    (
        "M2 the API lets an X-Org-Id header pick the tenant scope",
        "apps/api/src/onb_api/auth/dependencies.py",
        "    with tenant_scope(principal.org_id):\n        yield principal",
        '    with tenant_scope(request.headers.get("x-org-id", principal.org_id)):\n        yield principal',
    ),
    (
        "M3 login answers 403 (not 401) for a known email with a wrong password",
        "apps/api/src/onb_api/auth/routes.py",
        "    if user is None or not password_ok:\n        raise unauthenticated()",
        "    if user is not None and not password_ok:\n        raise __import__(\"fastapi\").HTTPException(status_code=403)\n"
        "    if user is None:\n        raise unauthenticated()",
    ),
    (
        "M4 mcp-mongo find runs in the wrong org's scope",
        "apps/mcp-mongo/src/onb_mcp_mongo/tools.py",
        "    with tenant_scope(ctx.org_id):\n        docs = await store.tenant(collection).find(",
        '    with tenant_scope("org_globex" if ctx.org_id == "org_acme" else ctx.org_id):\n'
        "        docs = await store.tenant(collection).find(",
    ),
    (
        "M8 the audit log total counts every org (AppendOnlyRepository.count drops the org filter)",
        "libs/py-data/src/onb_data/append_only.py",
        "        scoped = scoping.scoped_filter(query, current_tenant().org_id)\n"
        "        return await self._collection.count_documents(",
        "        scoped = dict(query or {})\n        return await self._collection.count_documents(",
    ),
    (
        "M5 the event worker runs a handler in another org's scope",
        "libs/py-eventbus/src/onb_eventbus/worker.py",
        "            tenant_scope(context.org_id),",
        '            tenant_scope("org_globex" if context.org_id == "org_acme" else context.org_id),',
    ),
    (
        "M6 a cross-tenant case is deleted (mcp-mongo count)",
        "tools/tests_xt/test_xt_mcp.py",
        '    ("mcp-mongo", "count"): ToolCase({"collection": "run_tasks"}, _count_check),\n',
        "",
    ),
    (
        "M7 /me finds the user by id across orgs (the user lookup leaves the token org scope)",
        "apps/api/src/onb_api/auth/mongo_users.py",
        "            doc = await self._store.tenant(USERS).get(user_id)",
        "            entry = await self._store.shared(LOGINS).find_one({'user_id': user_id})\n"
        "            with __import__('onb_data').tenant_scope(entry['org_id'] if entry else current_tenant().org_id):\n"
        "                doc = await self._store.tenant(USERS).get(user_id)",
    ),
    (
        "M9 the route enumerator keys routes by their path inside the router (prefix dropped)",
        "tools/tests_xt/surface.py",
        "route, path = context.original_route, context.path or repr(context.original_route)",
        'route, path = context.original_route, getattr(context.original_route, "path", "")',
    ),
    (
        "M10 the event worker keeps the first job's tenant for every later job",
        "libs/py-eventbus/src/onb_eventbus/worker.py",
        "            tenant_scope(context.org_id),",
        "            tenant_scope(self.__dict__.setdefault('_xt_first_org', context.org_id)),",
    ),
]


def env() -> dict[str, str]:
    values = dict(os.environ)
    for line in (ROOT / ".env").read_text().splitlines():
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2).strip("'")
    return values


def run() -> list[str]:
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "tools/tests_xt", "-q", "--tb=no", "-rfE", "-o", "cache_dir=.pytest_cache/xt-mut"],
        cwd=ROOT, env=env(), capture_output=True, text=True, check=False,
    )
    lines = [l for l in result.stdout.splitlines() if l.startswith(("FAILED", "ERROR"))]
    summary = [l for l in result.stdout.splitlines() if re.search(r"\d+ (passed|failed)", l)]
    return lines + summary[-1:]


def main() -> int:
    selected = sys.argv[1:]
    report = []
    for name, rel, old, new in MUTATIONS:
        if selected and name.split()[0] not in selected:
            continue
        path = ROOT / rel
        original = path.read_text()
        assert original.count(old) == 1, f"{name}: anchor not found once in {rel}"
        path.write_text(original.replace(old, new))
        try:
            lines = run()
        finally:
            path.write_text(original)
        killed = any(l.startswith("FAILED") or l.startswith("ERROR") for l in lines)
        report.append(f"## {name}\n{rel}\n  - {old.strip()[:110]}\n  + {new.strip()[:110]}\n" + "\n".join(lines) + f"\n=> {'KILLED' if killed else 'SURVIVED'}\n")
        print(report[-1], flush=True)
    OUT.write_text("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
