"""Break each REG-1 guard on purpose, run its tests, record which named tests failed, restore the file."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
AG = "libs/py-agents/src/onb_agents/registry/"
MUTATIONS = [
    ("M1 vault validators (before and after parse) off", AG + "manifest.py",
     "        if named:\n            raise ValueError(f\"a manifest may not name a vault collection",
     "        if False:\n            raise ValueError(f\"a manifest may not name a vault collection",
     "libs/py-agents/tests/test_manifest.py"),
    ("M2 vault match case-sensitive", AG + "manifest.py",
     "_WORDS.findall(text.lower())", "_WORDS.findall(text)", "libs/py-agents/tests/test_manifest.py"),
    ("M5 no version bump on update", AG + "sync.py",
     "            elif current[\"sha256\"] != manifest.sha256() or not current[\"active\"]:\n                version = int(current[\"version\"]) + 1",
     "            elif current[\"sha256\"] != manifest.sha256() or not current[\"active\"]:\n                version = int(current[\"version\"])",
     "libs/py-agents/tests/test_sync.py"),
    ("M6 change not audited", AG + "sync.py",
     "                await self._audit(write.change, orgs, actor)", "                pass",
     "libs/py-agents/tests/test_sync.py"),
    ("M7 audited in one org only", AG + "sync.py",
     "        for org in orgs:\n            with tenant_scope(org):",
     "        for org in orgs[:1]:\n            with tenant_scope(org):", "libs/py-agents/tests/test_sync.py"),
    ("M8 no-org check removed", AG + "sync.py",
     "        if not orgs:\n            raise RegistrySyncError", "        if False:\n            raise RegistrySyncError",
     "libs/py-agents/tests/test_sync.py"),
    ("M9 writes outside the transaction", AG + "sync.py",
     "        await self._store.atomically(apply)", "        await apply()", "libs/py-agents/tests/test_sync.py"),
    ("M10 stored manifest not re-validated", AG + "sync.py",
     'manifest = parse_manifest(document["manifest"])', 'manifest = AgentManifest.model_construct(**document["manifest"])',
     "libs/py-agents/tests/test_sync.py"),
    ("M11 retire skipped", AG + "sync.py",
     "            if name not in listed and current[\"active\"]:", "            if False:",
     "libs/py-agents/tests/test_sync.py"),
    ("M12 replace ignores the stored version", AG + "mongo.py",
     '{"_id": name, "version": expected_version}', '{"_id": name}', "libs/py-agents/tests/test_mongo_store.py"),
    ("M13 /agents open to HR", "apps/api/src/onb_api/agents/routes.py",
     "Depends(require_role(Role.HR_ADMIN))", "Depends(require_role(Role.HR_ADMIN, Role.HR))",
     "apps/api/tests/test_agents_route.py apps/api/tests/test_role_matrix.py"),
    ("M14 catalog drifts from the fixture", "config/tool_catalog.yaml",
     "{ action: assign_training, agent: db_agent }", "{ action: assign_training, agent: run_assistant }",
     "libs/py-agents/tests/test_config_manifests.py"),
    ("M15 catalog accepts unknown agents", AG + "catalog.py",
     "    if unknown:\n        raise ManifestError", "    if False:\n        raise ManifestError",
     "libs/py-agents/tests/test_config_manifests.py"),
]

for label, rel, old, new, tests in MUTATIONS:
    path = ROOT / rel
    original = path.read_text()
    if original.count(old) < 1:
        print(f"{label}: SKIPPED, pattern not found")
        continue
    path.write_text(original.replace(old, new))  # every occurrence (both vault validators for M1)
    try:
        result = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", *tests.split()],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
    finally:
        path.write_text(original)
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-200:]
    status = "CAUGHT" if result.returncode != 0 else "SURVIVED"
    print(f"{label}: {status} · {summary}")
    for name in failed[:3]:
        print(f"    {name.split('::', 1)[1]}")
