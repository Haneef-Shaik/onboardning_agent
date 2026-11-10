"""Guards for scripts/check_env_example.py (F-2 box 4, PRD §7.11: `.env.example` documents every variable)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_env_example.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_env_example", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load()


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("image: x:${TAG}", {"TAG"}),
        ("password: ${MONGO_PW:?set it}", {"MONGO_PW"}),
        ("port: ${PORT:-80}", {"PORT"}),
        ("port: ${PORT-80}", {"PORT"}),
        ("bind: ${ADDR:+x}", {"ADDR"}),
        ("cmd: echo $PLAIN_VAR", {"PLAIN_VAR"}),
        ("cmd: echo $$NOT_A_VAR and $${ALSO_NOT}", set()),
        # `#` lines are scanned too: inside a block scalar compose interpolates them, and a false
        # positive (document one variable too many) is safer than a missed one.
        ("cmd: |\n  # ${IN_BLOCK_SCALAR}\n  echo hi", {"IN_BLOCK_SCALAR"}),
        ("a: ${ONE}\nb: ${TWO:-${THREE}}", {"ONE", "TWO", "THREE"}),
    ],
)
def test_finds_interpolated_variables(checker: ModuleType, snippet: str, expected: set[str]) -> None:
    assert checker.compose_variables(snippet) == expected


def test_reads_keys_from_env_example(checker: ModuleType) -> None:
    text = "# comment\n\nFOO=1\nexport BAR='x'\nBAZ=\n  QUX=2\n"
    assert checker.example_keys(text) == {"FOO", "BAR", "BAZ", "QUX"}


def test_missing_variable_fails(checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    compose = tmp_path / "compose.yml"
    example = tmp_path / ".env.example"
    compose.write_text("services:\n  a:\n    image: x:${TAG}\n    environment:\n      PW: ${SECRET:?}\n")
    example.write_text("TAG=1\n")
    code = checker.main(["--compose", str(compose), "--env-example", str(example)])
    assert code == 1
    assert "SECRET" in capsys.readouterr().err


def test_complete_example_passes(checker: ModuleType, tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    example = tmp_path / ".env.example"
    compose.write_text("services:\n  a:\n    image: x:${TAG}\n")
    example.write_text("TAG=1\n")
    assert checker.main(["--compose", str(compose), "--env-example", str(example)]) == 0


def test_missing_files_fail_closed(checker: ModuleType, tmp_path: Path) -> None:
    assert checker.main(["--compose", str(tmp_path / "nope.yml"), "--env-example", str(tmp_path / "nope")]) == 2


def test_repo_env_example_covers_repo_compose() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_make_check_runs_env_example_check() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
    assert "env-check" in check_line.split()
    assert "scripts/check_env_example.py" in makefile
