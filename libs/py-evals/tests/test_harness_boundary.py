"""ADR 0003: the eval harness may seed the vault, so no running service may import the harness.

Two static checks over the repository's Python sources:
- nothing under `apps/` or another library imports `onb_evals`;
- inside `onb_evals`, only `vault_seed.py` imports a vault name (PII-2's guard allows just that file).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EVALS = REPO / "libs" / "py-evals"
SEEDER = EVALS / "src" / "onb_evals" / "vault_seed.py"
_VAULT_NAMES = frozenset({"Vault", "VaultStore", "VaultRepository"})
_VAULT_MODULES = frozenset({"onb_crypto.vault", "onb_crypto.vault_record", "onb_crypto.vault_indexes"})
_SKIP = frozenset({".venv", "node_modules", "__pycache__"})


def _imports(source: str) -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, []) for alias in node.names)
    return found


def _sources(*roots: Path) -> list[Path]:
    return [p for root in roots for p in root.rglob("*.py") if not _SKIP & set(p.parts)]


def _imports_harness(source: str) -> bool:
    return any(module == "onb_evals" or module.startswith("onb_evals.") for module, _ in _imports(source))


def _imports_vault(source: str) -> bool:
    return any(
        module in _VAULT_MODULES or (module == "onb_crypto" and _VAULT_NAMES & set(names))
        for module, names in _imports(source)
    )


def test_no_app_or_other_library_imports_the_harness() -> None:
    sources = [p for p in _sources(REPO / "apps", REPO / "libs") if not p.is_relative_to(EVALS)]
    assert len(sources) > 20, "the scan sees the repository"
    offenders = [str(p.relative_to(REPO)) for p in sources if _imports_harness(p.read_text())]
    assert offenders == []


def test_only_the_fixture_seeder_imports_the_vault_inside_the_harness() -> None:
    sources = _sources(EVALS / "src", EVALS / "tests")
    offenders = [str(p.relative_to(REPO)) for p in sources if p != SEEDER and _imports_vault(p.read_text())]
    assert offenders == []
    assert _imports_vault(SEEDER.read_text()), "the seeder is where the vault is used"


def test_the_scans_catch_each_import_form() -> None:
    assert _imports_harness("from onb_evals.vault_seed import seed_vault")
    assert _imports_harness("import onb_evals")
    assert not _imports_harness("import onb_evalsx")
    assert _imports_vault("from onb_crypto import Keyring, Vault")
    assert _imports_vault("import onb_crypto.vault_record as r")
    assert not _imports_vault("from onb_crypto import is_token")
