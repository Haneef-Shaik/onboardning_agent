"""Guards for scripts/contracts.py: the committed OpenAPI and TS client must match the API (C-1, PRD §11)."""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "contracts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contracts_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contracts() -> ModuleType:
    return _load()


@pytest.fixture
def committed_copy(contracts: ModuleType, tmp_path: Path) -> tuple[Path, Path]:
    openapi = tmp_path / "openapi.json"
    schema = tmp_path / "schema.ts"
    shutil.copyfile(contracts.OPENAPI, openapi)
    shutil.copyfile(contracts.SCHEMA, schema)
    return openapi, schema


def test_the_committed_contracts_match_the_api(contracts: ModuleType) -> None:
    assert contracts.drifted(contracts.OPENAPI, contracts.SCHEMA) == []


def test_a_stale_openapi_document_is_drift(contracts: ModuleType, committed_copy: tuple[Path, Path]) -> None:
    openapi, schema = committed_copy
    openapi.write_text(openapi.read_text(encoding="utf-8").replace('"getHealth"', '"getHealthOld"'), encoding="utf-8")
    assert contracts.drifted(openapi, schema) == [openapi]


def test_a_stale_ts_client_is_drift(contracts: ModuleType, committed_copy: tuple[Path, Path]) -> None:
    openapi, schema = committed_copy
    schema.write_text(schema.read_text(encoding="utf-8") + "// edited by hand\n", encoding="utf-8")
    assert contracts.drifted(openapi, schema) == [schema]


def test_a_missing_committed_file_is_drift(contracts: ModuleType, committed_copy: tuple[Path, Path]) -> None:
    openapi, schema = committed_copy
    schema.unlink()
    assert contracts.drifted(openapi, schema) == [schema]


def test_check_fails_with_a_diff_and_the_fix(
    contracts: ModuleType, committed_copy: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    openapi, schema = committed_copy
    openapi.write_text("{}\n", encoding="utf-8")
    assert contracts.check(openapi, schema) == 1
    err = capsys.readouterr().err
    assert "make contracts" in err
    assert f"--- {openapi} (committed)" in err


def test_check_passes_on_current_files(contracts: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert contracts.check(contracts.OPENAPI, contracts.SCHEMA) == 0
    assert "up to date" in capsys.readouterr().out


def test_generate_writes_what_check_expects(contracts: ModuleType, tmp_path: Path) -> None:
    openapi = tmp_path / "out" / "openapi.json"
    schema = tmp_path / "out" / "generated" / "schema.ts"
    contracts.generate(openapi, schema)
    assert contracts.drifted(openapi, schema) == []
    assert openapi.read_bytes() == contracts.OPENAPI.read_bytes()


def test_a_missing_generator_fails_closed(
    contracts: ModuleType, committed_copy: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contracts, "GENERATOR", ROOT / "node_modules" / ".bin" / "no-such-generator")
    with pytest.raises(FileNotFoundError, match="npm ci"):
        contracts.drifted(*committed_copy)


@pytest.mark.parametrize("argv", [[], ["publish"], ["check", "extra"]])
def test_main_rejects_unknown_usage(contracts: ModuleType, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert contracts.main(argv) == 2
    assert "usage" in capsys.readouterr().err


def test_makefile_has_contracts_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^contracts:", makefile, re.M)
    assert re.search(r"^contracts-check:", makefile, re.M)
    assert "scripts/contracts.py check" in makefile
    assert "scripts/contracts.py generate" in makefile
