"""Mutation checks for F-3's guards: apply each mutation, run the guard tests, restore the file."""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(sys.argv[1])
WF = ROOT / ".github/workflows/ci.yml"
ADR = ROOT / "docs/adr/0009-ci-host.md"
MAKEFILE = ROOT / "Makefile"
ENV = "      - run: uv run --frozen python tools/ci_env.py > .env\n"
MUTATIONS = [
    ("M1 push limited to main", WF, "  push:\n", "  push:\n    branches: [main]\n"),
    ("M2 integration no longer needs check", WF, "    needs: check\n", ""),
    ("M3 checkout unpinned to a tag", WF, "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v7"),
    ("M4 integration job renamed", WF, "  integration:\n", "  int-tests:\n"),
    ("M5 make check dropped from the check job", WF, "      - run: make check\n", ""),
    ("M6 token widened to write", WF, "contents: read", "contents: write"),
    ("M7 make test-int dropped from integration", WF, "      - run: make test-int\n", ""),
    ("M8 ADR protection payload drops the integration check", ADR, '["check", "integration", "pii"]', '["check", "pii"]'),
    ("M9 integration display name changed", WF, "    name: integration\n", "    name: Integration tests\n"),
    ("M10 ADR protection no longer enforced for admins", ADR, '"enforce_admins": true', '"enforce_admins": false'),
    ("M11 make check allowed to fail", WF, "      - run: make check\n", "      - run: make check\n        continue-on-error: true\n"),
    ("M12 integration job skipped by a condition", WF, "    needs: check\n", "    needs: check\n    if: false\n"),
    ("M13 runs on main cancelled too", WF, "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}", "cancel-in-progress: true"),
    ("M14 restore key no longer a prefix of the key", WF, "          restore-keys: |\n            nx-", "          restore-keys: |\n            other-"),
    ("M15 check job no longer writes the runner .env", WF, ENV + "      - run: make check\n", "      - run: make check\n"),
    ("M16 contracts-check dropped from CI", WF, "      - run: make contracts-check\n", ""),
    ("M17 evals-selftest dropped from CI", WF, "      - run: make evals-selftest\n", ""),
    ("M18 runner .env written after make test-int", WF, ENV + "      - run: make test-int\n", "      - run: make test-int\n" + ENV),
    ("M19 gate step made conditional with always()", WF, "      - run: make test-int\n", "      - run: make test-int\n        if: always()\n"),
    ("M20 ADR protection payload drops pii", ADR, '["check", "integration", "pii"]', '["check", "integration"]'),
    ("M21 make check no longer runs test-xt", MAKEFILE, "typecheck test test-xt\n", "typecheck test\n"),
    ("M22 pii job removed from CI", WF, "  pii:\n    name: pii\n", "  pii-off:\n    name: pii-off\n"),
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
