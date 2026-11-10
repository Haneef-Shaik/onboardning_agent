"""Only the API layer holds a `Vault` (PRD §7.10 Writers): no agent, MCP server, orchestrator or other
library imports the vault. A static check over the repository's Python sources."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ALLOWED = (REPO / "libs" / "py-crypto", REPO / "apps" / "api")
_VAULT_NAMES = frozenset({"Vault", "VaultStore", "VaultRepository"})
_VAULT_MODULES = frozenset({"onb_crypto.vault", "onb_crypto.vault_record", "onb_crypto.vault_indexes"})


def _vault_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in _VAULT_MODULES:
                found.append(node.module)
            elif node.module == "onb_crypto":
                found.extend(a.name for a in node.names if a.name in _VAULT_NAMES)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name in _VAULT_MODULES)
    return found


def _python_sources() -> list[Path]:
    roots = [REPO / "apps", REPO / "libs"]
    skip = {".venv", "node_modules", "__pycache__"}
    return [
        path
        for root in roots
        for path in root.rglob("*.py")
        if not skip & set(path.parts) and not any(path.is_relative_to(a) for a in ALLOWED)
    ]


def test_no_module_outside_the_api_imports_the_vault() -> None:
    sources = _python_sources()
    assert len(sources) > 20, "the scan sees the repository"
    offenders = {str(p.relative_to(REPO)): hits for p in sources if (hits := _vault_imports(p.read_text()))}
    assert offenders == {}


@pytest.mark.parametrize(
    "source",
    [
        "from onb_crypto import Vault",
        "from onb_crypto import Keyring, VaultStore",
        "from onb_crypto.vault import Vault",
        "import onb_crypto.vault",
        "import onb_crypto.vault_record as r",
    ],
)
def test_the_scan_catches_each_import_form(source: str) -> None:
    assert _vault_imports(source)


def test_the_scan_allows_tokens_and_masks() -> None:
    assert _vault_imports("from onb_crypto import is_token, mask_mobile, PURGED") == []
