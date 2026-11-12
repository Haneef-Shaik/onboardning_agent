"""Print a `.env` for a CI runner (SEC-1: the `pii` workflow runs `make test-pii` on a fresh machine).

Starts from `.env.example`, keeps its non-secret defaults, fills every empty value with a fresh random one
(or the fake LLM connector's placeholder), and moves Caddy to unprivileged ports. Nothing it prints is a
real credential: the values exist only for that one CI job. Usage: `python tools/ci_env.py > .env`.
"""

from __future__ import annotations

import base64
import re
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import bcrypt

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE: Final = ROOT / ".env.example"
_LINE: Final = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
OVERRIDES: Final[Mapping[str, str]] = {
    "COMPOSE_PROJECT_NAME": "onb-ci",
    "CADDY_HTTP_PORT": "8080",
    "CADDY_HTTPS_PORT": "8443",
    "LLM_CONNECTOR": "fake",
    "LLM_MODEL_FAST": "fake-fast",
    "LLM_MODEL_STANDARD": "fake-standard",
    "LLM_MODEL_DEEP": "fake-deep",
    "AWS_REGION": "unused",
}
# Optional values that stay empty: `make seed` tunnels when MONGO_URI is empty; no Bedrock guardrail or script.
LEFT_EMPTY: Final = frozenset(
    {"MONGO_URI", "BEDROCK_GUARDRAIL_ID", "BEDROCK_GUARDRAIL_VERSION", "LLM_FAKE_SCRIPT_FILE"}
)


def _generated(name: str) -> str:
    if name == "PII_MASTER_KEY":
        return base64.b64encode(secrets.token_bytes(32)).decode()
    if name == "JAEGER_BASIC_AUTH_HASH":
        return "'" + bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode() + "'"
    return secrets.token_hex(16)


def ci_env(example: str) -> str:
    lines = []
    for line in example.splitlines():
        match = _LINE.match(line)
        if match is None:
            lines.append(line)
            continue
        name, value = match.groups()
        if name in OVERRIDES:
            value = OVERRIDES[name]
        elif not value and name not in LEFT_EMPTY:
            value = _generated(name)
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.write(ci_env(EXAMPLE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
