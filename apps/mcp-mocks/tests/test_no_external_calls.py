"""Nothing in mcp-mocks can call a real external API (PRD §2 Non-goals, MOCK-1 outcome).

The package imports no HTTP client, socket or vendor SDK, so no code path can reach Telegram,
Bitbucket or an IT provisioning system. (uvicorn serves the server; it only listens.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import onb_mcp_mocks

SRC = Path(onb_mcp_mocks.__file__).parent
FORBIDDEN = (
    "aiohttp",
    "atlassian",
    "boto3",
    "botocore",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "telegram",
    "telethon",
    "urllib.request",
    "urllib3",
    "websockets",
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN)


def test_the_package_imports_no_network_client_or_vendor_sdk() -> None:
    sources = sorted(SRC.rglob("*.py"))
    assert len(sources) >= 6
    found = {f"{p.name}: {name}" for p in sources for name in _imports(p) if _forbidden(name)}
    assert found == set()


def test_the_scan_would_catch_one() -> None:
    assert _forbidden("httpx") and _forbidden("urllib.request") and _forbidden("telegram.ext")
    assert not _forbidden("onb_mcp_mocks.telegram") and not _forbidden("uvicorn")
