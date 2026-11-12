"""SEC-1: the tamper report's manifest stays complete. Every listed case exists, and every test in apps/ and
libs/ whose name says it tampers, forges or widens something is listed or excluded with a reason, so a new
tamper case cannot silently stay out of `make test-pii`'s report. Reads files only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pii_tamper", ROOT / "tools" / "tests_pii" / "tamper.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tamper = _load()


def test_the_manifest_is_complete_and_every_case_exists() -> None:
    manifest = tamper.Manifest.load()
    assert tamper.guard_problems(manifest) == []
    assert set(manifest.categories) == {"vault", "grant", "token", "document"}
    assert len(manifest.cases()) >= 100


def _repo(tmp: Path, tests: str) -> Path:
    folder = tmp / "libs" / "py-x" / "tests"
    folder.mkdir(parents=True)
    (folder / "test_x.py").write_text(tests)
    return tmp


def _manifest(cases: list[str], excluded: dict[str, str] | None = None) -> object:
    return tamper.Manifest({"vault": ("Vault", tuple(cases))}, excluded or {})


def test_an_unlisted_tamper_test_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path, "def test_a_tampered_record_fails() -> None: ...\ndef test_other() -> None: ...\n")
    assert tamper.guard_problems(_manifest([]), root) == [
        "tamper-named test is neither listed nor excluded: libs/py-x/tests/test_x.py::test_a_tampered_record_fails"
    ]


def test_a_listed_case_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path, "def test_a_tampered_record_fails() -> None: ...\n")
    listed = ["libs/py-x/tests/test_x.py::test_a_tampered_record_fails", "libs/py-x/tests/test_x.py::test_gone"]
    assert tamper.guard_problems(_manifest(listed), root) == [
        "listed but not found: libs/py-x/tests/test_x.py::test_gone"
    ]


def test_an_excluded_tamper_test_needs_no_listing_but_cannot_also_be_listed(tmp_path: Path) -> None:
    root = _repo(tmp_path, "def test_a_forged_dag_is_refused() -> None: ...\n")
    case = "libs/py-x/tests/test_x.py::test_a_forged_dag_is_refused"
    assert tamper.guard_problems(_manifest([], {case: "not PII"}), root) == []
    assert tamper.guard_problems(_manifest([case], {case: "not PII"}), root) == [f"both listed and excluded: {case}"]


def test_a_case_runs_in_its_own_test_folder() -> None:
    assert tamper.folder_of("libs/py-crypto/tests/test_vault.py::test_x") == "libs/py-crypto/tests"
    assert tamper.folder_of("apps/api/tests_int/test_mongo_documents.py::test_x") == "apps/api/tests_int"


def test_the_report_counts_only_passed_cases_as_rejected() -> None:
    manifest = _manifest(["a::t1", "a::t2"])
    outcomes = [
        tamper.Outcome("vault", "a::t1", True, "rejected"),
        tamper.Outcome("vault", "a::t2", False, "failure"),
        tamper.Outcome("vault", "a::t3", False, "did not run"),
    ]
    body = tamper.report(manifest, outcomes)
    assert (body["cases"], body["rejected"], body["rejected_percent"]) == (3, 1, 33.33)
    assert body["categories"]["vault"]["rejected"] == "1/3"
