"""The SEC-1 tamper report: run every case of `tamper_cases.yaml` and list each with its outcome.

A case is **rejected** when its test passes: each test asserts that the platform refused the tampered,
forged, widened, replayed or expired input. Anything else (a failure, an error, a skip, a case that did not
run) is listed as not rejected, so 100% means every listed case ran and every one was refused.

Cases run per test folder (each folder's own conftest and fixtures), one pytest process each.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

HERE: Final = Path(__file__).resolve().parent
ROOT: Final = HERE.parents[1]
MANIFEST: Final = HERE / "tamper_cases.yaml"
REPORT_NAME: Final = "tamper-report"
# A test whose name says one of these is a tamper case: it must be listed or excluded with a reason.
GUARD: Final = re.compile(
    r"tamper|forge(?!t)|widen|swap|signature|unsigned|alg_none|none_algorithm|another_algorithm|flipped"
    r"|fails_to_decrypt|rebind|did_not_issue|not_granted"
)
SEARCHED: Final = ("apps", "libs")
FOLDER_TIMEOUT_S: Final = 600


@dataclass(frozen=True)
class Manifest:
    categories: Mapping[str, tuple[str, tuple[str, ...]]]  # name -> (title, node ids)
    excluded: Mapping[str, str]  # node id -> reason

    @classmethod
    def load(cls, path: Path = MANIFEST) -> Manifest:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        categories = {
            name: (str(body["title"]), tuple(str(case) for case in body["cases"]))
            for name, body in raw["categories"].items()
        }
        return cls(categories, {str(k): str(v) for k, v in (raw.get("excluded") or {}).items()})

    def cases(self) -> list[tuple[str, str]]:
        """(category, node id) for every listed case, in manifest order."""
        return [(name, case) for name, (_, cases) in self.categories.items() for case in cases]


def discover_tests(root: Path) -> dict[str, Path]:
    """Every `test_*` function under apps/ and libs/ test folders: node id -> file."""
    found: dict[str, Path] = {}
    for top in SEARCHED:
        for path in sorted((root / top).glob("*/tests*/**/test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                    found[f"{path.relative_to(root).as_posix()}::{node.name}"] = path
    return found


def guard_problems(manifest: Manifest, root: Path = ROOT) -> list[str]:
    """Listed cases that do not exist, tamper-named tests that are neither listed nor excluded, and a case
    listed twice or both listed and excluded."""
    existing = discover_tests(root)
    listed = [case for _, case in manifest.cases()]
    problems = [f"listed but not found: {case}" for case in listed if case not in existing]
    problems += [f"excluded but not found: {case}" for case in manifest.excluded if case not in existing]
    problems += [f"listed twice: {case}" for case in sorted({c for c in listed if listed.count(c) > 1})]
    problems += [f"both listed and excluded: {case}" for case in sorted(set(listed) & set(manifest.excluded))]
    known = set(listed) | set(manifest.excluded)
    unlisted = [node for node in existing if GUARD.search(node.split("::")[1]) and node not in known]
    problems += [f"tamper-named test is neither listed nor excluded: {node}" for node in unlisted]
    return problems


def folder_of(case: str) -> str:
    """The test folder a case runs in: `libs/py-crypto/tests`, `apps/api/tests_int`, …"""
    parts = case.split("::")[0].split("/")
    return "/".join(parts[:3])


@dataclass(frozen=True)
class Outcome:
    category: str
    case: str  # node id, with the parameter id for a parametrized case
    rejected: bool
    detail: str


def run_cases(manifest: Manifest, workdir: Path) -> list[Outcome]:
    """Run every case, one pytest process per test folder; one outcome per collected test item."""
    by_folder: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for category, case in manifest.cases():
        by_folder[folder_of(case)].append((category, case))
    outcomes: list[Outcome] = []
    for folder, cases in by_folder.items():
        outcomes += _run_folder(folder, cases, workdir)
    return outcomes


def _run_folder(folder: str, cases: list[tuple[str, str]], workdir: Path) -> list[Outcome]:
    junit = workdir / f"junit-{folder.replace('/', '_')}.xml"
    argv = [
        sys.executable, "-m", "pytest", *[case for _, case in cases], "-q", "-p", "no:cacheprovider",
        "-o", "junit_family=xunit1", f"--junitxml={junit}", "--no-cov",
    ]  # fmt: skip
    try:
        subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False, timeout=FOLDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return [Outcome(category, case, False, f"timed out after {FOLDER_TIMEOUT_S}s") for category, case in cases]
    seen = _junit_outcomes(junit) if junit.is_file() else {}
    outcomes: list[Outcome] = []
    for category, case in cases:
        items = {node: result for node, result in seen.items() if node == case or node.startswith(f"{case}[")}
        if not items:
            outcomes.append(Outcome(category, case, False, "did not run"))
            continue
        outcomes += [Outcome(category, node, ok, detail) for node, (ok, detail) in sorted(items.items())]
    return outcomes


def _junit_outcomes(path: Path) -> dict[str, tuple[bool, str]]:
    found: dict[str, tuple[bool, str]] = {}
    for testcase in ET.parse(path).getroot().iter("testcase"):  # noqa: S314 - our own pytest output
        file, name = testcase.get("file", ""), testcase.get("name", "")
        problem = next((child for child in testcase if child.tag in {"failure", "error", "skipped"}), None)
        if problem is None:
            found[f"{file}::{name}"] = (True, "rejected")
        else:
            message = (problem.get("message") or "").splitlines()[0][:200] if problem.get("message") else ""
            found[f"{file}::{name}"] = (False, f"{problem.tag}: {message}" if message else problem.tag)
    return found


def report(manifest: Manifest, outcomes: Iterable[Outcome]) -> dict[str, Any]:
    outcomes = list(outcomes)
    rejected = sum(o.rejected for o in outcomes)
    by_category: dict[str, dict[str, Any]] = {}
    for name, (title, _) in manifest.categories.items():
        cases = [{"case": o.case, "result": o.detail} for o in outcomes if o.category == name]
        passed = sum(case["result"] == "rejected" for case in cases)
        by_category[name] = {"title": title, "rejected": f"{passed}/{len(cases)}", "cases": cases}
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "what": "SEC-1 tamper report: every vault, grant, token and document tamper case, run by make test-pii",
        "cases": len(outcomes),
        "rejected": rejected,
        "rejected_percent": round(100 * rejected / len(outcomes), 2) if outcomes else 0.0,
        "categories": by_category,
        "excluded": dict(manifest.excluded),
    }


def write(directory: Path, body: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{REPORT_NAME}.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Tamper report (SEC-1, `make test-pii`)",
        "",
        f"Generated {body['generated_at']}. **{body['rejected']}/{body['cases']} rejected "
        f"({body['rejected_percent']}%).** A case is rejected when its test, which asserts the refusal, passes.",
        "",
    ]
    for name, category in body["categories"].items():
        lines += [f"## {name}: {category['title']} ({category['rejected']})", "", "| Case | Result |", "|---|---|"]
        lines += [f"| `{c['case']}` | {c['result']} |" for c in category["cases"]]
        lines.append("")
    lines += ["## Excluded tamper-named tests", ""]
    lines += [f"- `{case}`: {reason}" for case, reason in body["excluded"].items()]
    (directory / f"{REPORT_NAME}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
