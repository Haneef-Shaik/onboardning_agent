"""TPL-2 mutation checks: break each guard on purpose, run the tests, record which ones failed.

Run from the repo root: `uv run --frozen python docs/evidence/TPL-2/mutate.py` (the `int` mutations
need the compose stack and the `.env` variables in the environment, as `make test-int` has them). Each
mutation is one exact text replacement that must match once. The file is restored after each run, and
the script checks at the end that no source file differs from git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PKG = "apps/api/src/onb_api/templates"
ROUTES = f"{PKG}/routes.py"
LIFE = f"{PKG}/lifecycle.py"
STORE = f"{PKG}/store.py"
MEMORY = f"{PKG}/memory.py"
LIMIT = "apps/api/src/onb_api/body_limit.py"
SHAPE = "libs/py-contracts/src/onb_contracts/templates.py"
SUITES = {
    "unit": ["apps/api/tests"],
    "int": ["apps/api/tests_int/test_mongo_templates.py"],
    "contracts": ["libs/py-contracts/tests/test_template_contracts.py"],
}

# (id, guard, suite, [(file, old, new), ...])
Mutation = tuple[str, str, str, list[tuple[str, str, str]]]
MUTATIONS: list[Mutation] = [
    (
        "M1",
        "HR may publish",
        "unit",
        [(ROUTES, "if principal.role is Role.HR_ADMIN:", "if principal.role in (Role.HR_ADMIN, Role.HR):")],
    ),
    ("M2", "anyone may publish", "unit", [(ROUTES, "if principal.role is Role.HR_ADMIN:", "if True:")]),
    (
        "M3",
        "publish skips validation",
        "unit",
        [(LIFE, "if not report.ok or report.template is None:", "if report.template is None:")],
    ),
    (
        "M4",
        "422 without the validator's errors",
        "unit",
        [(ROUTES, "for issue in error.issues\n", "for issue in ()\n")],
    ),
    (
        "M5",
        "422 becomes 409",
        "unit",
        [
            (
                ROUTES,
                "    except TemplateRejectedError as error:\n        raise _invalid(error) from None\n",
                "    except TemplateRejectedError:\n        raise AppError(ErrorCode.CONFLICT) from None\n",
            )
        ],
    ),
    (
        "M6",
        "published draft editable (lifecycle and store checks both off)",
        "unit",
        [
            (
                LIFE,
                '        if current.status != "draft":\n            raise DraftLockedError',
                "        if False:\n            raise DraftLockedError",
            ),
            (
                MEMORY,
                'if current is None or current.revision != expected_revision or current.status != "draft":',
                "if current is None or current.revision != expected_revision:",
            ),
        ],
    ),
    (
        "M7",
        "in-memory store overwrites a version",
        "unit",
        [(MEMORY, "        if key in self._state.versions:\n", "        if False:\n")],
    ),
    (
        "M8",
        "in-memory atomically does not roll back",
        "unit",
        [(MEMORY, "            self._state = before\n", "            pass\n")],
    ),
    (
        "M9",
        "publish not audited",
        "unit",
        [
            (
                LIFE,
                "            target=version_target(template.key, number),\n            outcome=AuditOutcome.SUCCESS,",
                "            target=version_target(template.key, number),\n            outcome=AuditOutcome.SUCCESS if False else AuditOutcome.FAILED,",
            )
        ],
    ),
    (
        "M10",
        "refused publish not audited",
        "unit",
        [
            (
                ROUTES,
                "        lifecycle = template_lifecycle(request)\n        await lifecycle.auditor.audit(",
                "        lifecycle = template_lifecycle(request)\n        None and await lifecycle.auditor.audit(",
            )
        ],
    ),
    (
        "M11",
        "failed publish not audited",
        "unit",
        [
            (
                LIFE,
                '                outcome=AuditOutcome.FAILED,\n                reason=f"validation failed',
                '                outcome=AuditOutcome.DENIED,\n                reason=f"validation failed',
            )
        ],
    ),
    (
        "M12",
        "HR may archive",
        "unit",
        [
            (
                ROUTES,
                "Archiver = Annotated[Principal, Depends(_admin_audited(ARCHIVE_ACTION, _version_target))]",
                "Archiver = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR))]",
            )
        ],
    ),
    (
        "M13",
        "archive not audited",
        "unit",
        [
            (
                LIFE,
                "                action=ARCHIVE_ACTION,\n                target=version_target(key, version),\n                outcome=AuditOutcome.SUCCESS,",
                "                action=PUBLISH_ACTION,\n                target=version_target(key, version),\n                outcome=AuditOutcome.SUCCESS,",
            )
        ],
    ),
    (
        "M14",
        "drafts with sensitive data stored",
        "unit",
        [
            (
                LIFE,
                "        if sensitive:\n            raise DraftRefusedError(sensitive)",
                "        if False:\n            raise DraftRefusedError(sensitive)",
            )
        ],
    ),
    (
        "M15",
        "stale revision accepted",
        "unit",
        [(LIFE, "if revision is not None and revision != current.revision:", "if False:")],
    ),
    (
        "M16",
        "re-publish not idempotent",
        "unit",
        [(LIFE, 'if draft.status == "published" and draft.published_version is not None:', "if False:")],
    ),
    (
        "M17",
        "managers author drafts",
        "unit",
        [
            (
                ROUTES,
                "Author = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR))]",
                "Author = Annotated[Principal, Depends(require_role(Role.HR_ADMIN, Role.HR, Role.MANAGER))]",
            )
        ],
    ),
    (
        "M18",
        "draft lock not taken on publish",
        "unit",
        [
            (
                LIFE,
                '        if not await self._store.replace_draft(locked, expected_revision=draft.revision):\n            raise LifecycleConflictError("the draft changed while publishing")',
                '        if False:\n            raise LifecycleConflictError("the draft changed while publishing")',
            )
        ],
    ),
    (
        "M19",
        "lifecycle fields kept from the body",
        "unit",
        [
            (
                LIFE,
                '    return template.model_dump(mode="json", exclude=set(_LIFECYCLE_FIELDS))',
                '    return template.model_dump(mode="json")',
            )
        ],
    ),
    (
        "M20",
        "archive changes the version body",
        "unit",
        [
            (
                LIFE,
                'template = Template.model_validate({**stored.template, "version": stored.version, "status": status})',
                'template = Template.model_validate({**stored.template, "version": stored.version, "status": status, "name": f"{status} copy"})',
            )
        ],
    ),
    (
        "M21",
        "Mongo version index not unique",
        "int",
        [
            (
                STORE,
                'VERSIONS, [("org_id", 1), ("key", 1), ("version", 1)], unique=True, name="org_key_version"',
                'VERSIONS, [("org_id", 1), ("key", 1), ("version", 1)], unique=False, name="org_key_version"',
            )
        ],
    ),
    (
        "M22",
        "Mongo publish outside a transaction",
        "int",
        [(STORE, "        return await self._store.run_transaction(callback)", "        return await callback()")],
    ),
    (
        "M23",
        "Mongo draft lock ignores status",
        "int",
        [
            (
                STORE,
                '{"_id": draft.id, "revision": expected_revision, "status": "draft"}',
                '{"_id": draft.id, "revision": expected_revision}',
            ),
            (
                LIFE,
                '        if current.status != "draft":\n            raise DraftLockedError',
                "        if False:\n            raise DraftLockedError",
            ),
        ],
    ),
    (
        "M24",
        "Mongo version insert overwrites (upsert by key and version)",
        "int",
        [
            (
                STORE,
                "            await self._store.tenant(VERSIONS).insert_one(version.document())",
                '            await self._store.tenant(VERSIONS).update_one({"key": version.key, "version": version.version}, {"$set": {k: v for k, v in version.document().items() if k != "_id"}}, upsert=True)',
            )
        ],
    ),
    # Review fixes (security H1, code review L5/L7/L8).
    (
        "M25",
        "declared body length not checked",
        "unit",
        [(LIMIT, "        if declared is not None and declared > self.limit:\n", "        if False:\n")],
    ),
    (
        "M26",
        "streamed body not counted",
        "unit",
        [(LIMIT, "            if len(body) > self.limit:\n", "            if False:\n")],
    ),
    (
        "M27",
        "numbers count as one character",
        "contracts",
        [
            (
                SHAPE,
                "    return len(repr(value)) if isinstance(value, int | float) and not isinstance(value, bool) else 1\n",
                "    return 1\n",
            )
        ],
    ),
    (
        "M28",
        "integers beyond int64 accepted",
        "contracts",
        [(SHAPE, "and not _INT64_MIN <= value <= _INT64_MAX:", "and False:")],
    ),
    (
        "M29",
        "lists in inputs unbounded",
        "contracts",
        [
            (
                SHAPE,
                '        if len(value) > MAX_LIST:\n            raise ValueError(f"an inputs list',
                '        if False:\n            raise ValueError(f"an inputs list',
            )
        ],
    ),
    (
        "M30",
        "existing version is a 500",
        "unit",
        [(ROUTES, "    except VersionExistsError:\n", "    except ZeroDivisionError:\n")],
    ),
    (
        "M31",
        "in-memory store hands out its own body",
        "unit",
        [
            (
                MEMORY,
                "return None if found is None else replace(found, template=copy.deepcopy(dict(found.template)))",
                "return found",
            )
        ],
    ),
    (
        "M32",
        "out-of-range version audited as a real target",
        "unit",
        [(ROUTES, " and int(version) <= MAX_TEMPLATE_VERSION:", ":")],
    ),
]


def _failed(output: str) -> list[str]:
    return [
        line.split(" ", 1)[1].split(" - ")[0] for line in output.splitlines() if line.startswith(("FAILED ", "ERROR "))
    ]


def _apply(edits: list[tuple[str, str, str]]) -> dict[Path, str] | str:
    originals: dict[Path, str] = {}
    for rel, old, new in edits:
        path = REPO / rel
        source = originals.get(path, path.read_text(encoding="utf-8"))
        current = path.read_text(encoding="utf-8")
        if current.count(old) != 1:
            for restored, text in originals.items():
                restored.write_text(text, encoding="utf-8")
            return f"pattern found {current.count(old)} times in {rel}"
        originals.setdefault(path, source)
        path.write_text(current.replace(old, new), encoding="utf-8")
    return originals


def main() -> int:
    survivors = []
    for mutation_id, guard, suite, edits in MUTATIONS:
        applied = _apply(edits)
        if isinstance(applied, str):
            print(f"{mutation_id} SETUP ERROR: {applied}")
            return 2
        try:
            run = subprocess.run(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "pytest",
                    *SUITES[suite],
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-rfE",
                    "--no-header",
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            for path, text in applied.items():
                path.write_text(text, encoding="utf-8")
        failed = _failed(run.stdout)
        summary = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip()[-200:]
        if failed or run.returncode != 0:
            shown = ", ".join(failed[:3]) + (f" (+{len(failed) - 3} more)" if len(failed) > 3 else "")
            print(f"{mutation_id} KILLED  [{suite}] {guard}: {shown or summary}")
        else:
            survivors.append(mutation_id)
            print(f"{mutation_id} SURVIVED [{suite}] {guard}: {summary}")
    diff = subprocess.run(
        ["git", "diff", "--stat", "--", "apps", "libs"], cwd=REPO, capture_output=True, text=True, check=True
    )
    changed = [line for line in diff.stdout.splitlines() if "|" in line]
    print(f"source diff after all mutations: {len(changed)} files differ")
    print(f"mutations: {len(MUTATIONS)}, killed: {len(MUTATIONS) - len(survivors)}, survivors: {survivors or 'none'}")
    return 1 if survivors or changed else 0


if __name__ == "__main__":
    sys.exit(main())
