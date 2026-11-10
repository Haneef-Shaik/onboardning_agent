"""Mutation checks for F-3's guards: apply each mutation, run the guard tests, restore the file."""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(sys.argv[1])
WF = ROOT / ".github/workflows/ci.yml"
ADR = ROOT / "docs/adr/0001-ci-host.md"
MUTATIONS = [
    ("M1 push limited to main", WF, "  push:\n", "  push:\n    branches: [main]\n"),
    ("M2 integration no longer needs check", WF, "    needs: check\n", ""),
    ("M3 checkout unpinned to a tag", WF, "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v7"),
    ("M4 integration job renamed", WF, "  integration:\n", "  int-tests:\n"),
    ("M5 make check dropped from the check job", WF, "      - run: make check\n", ""),
    ("M6 token widened to write", WF, "contents: read", "contents: write"),
    ("M7 make test-int dropped from integration", WF, "      - run: make test-int\n", ""),
    ("M8 ADR protection payload drops the integration check", ADR, '["check", "integration"]', '["check"]'),
    ("M9 integration display name changed", WF, "    name: integration\n", "    name: Integration tests\n"),
    ("M10 ADR protection no longer enforced for admins", ADR, '"enforce_admins": true', '"enforce_admins": false'),
]
for name, path, old, new in MUTATIONS:
    original = path.read_text()
    assert old in original, name
    path.write_text(original.replace(old, new, 1))
    try:
        res = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "tools/tests/test_ci_workflow.py", "-q"],
            cwd=ROOT, capture_output=True, text=True,
        )
    finally:
        path.write_text(original)
    lines = [l for l in res.stdout.splitlines() if re.match(r"^(FAILED|\d+ (passed|failed))", l)]
    print(f"=== {name}  (pytest exit={res.returncode})")
    print("\n".join(lines))
    print(f"restored: {path.read_text() == original}")
