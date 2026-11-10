#!/usr/bin/env python3
"""Fail when a variable the compose file interpolates is not documented in `.env.example`.

PRD §7.11: "`.env.example` documents every variable". Run by `make check` (target `env-check`).
Exit codes: 0 all documented, 1 something missing, 2 an input file is missing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE = ROOT / "infra" / "docker-compose.yml"
DEFAULT_ENV_EXAMPLE = ROOT / ".env.example"

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_BRACED = re.compile(rf"\$\{{({_NAME})")
_BARE = re.compile(rf"\$({_NAME})")
_ESCAPED = re.compile(r"\$\$")
_ENV_LINE = re.compile(rf"^\s*(?:export\s+)?({_NAME})\s*=")


def compose_variables(text: str) -> set[str]:
    """Names interpolated as `${VAR…}` or `$VAR`; `$$` escapes are ignored.

    `#` lines are scanned too: compose interpolates them inside block scalars, and documenting a
    variable that only a comment mentions is a safer mistake than missing one.
    """
    body = _ESCAPED.sub("", text)
    return set(_BRACED.findall(body)) | set(_BARE.findall(body))


def example_keys(text: str) -> set[str]:
    """Keys assigned in a dotenv file (`KEY=…` or `export KEY=…`)."""
    return {m.group(1) for line in text.splitlines() if (m := _ENV_LINE.match(line))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--env-example", type=Path, default=DEFAULT_ENV_EXAMPLE)
    args = parser.parse_args(argv)

    for path in (args.compose, args.env_example):
        if not path.is_file():
            print(f"check_env_example: {path} not found", file=sys.stderr)
            return 2

    used = compose_variables(args.compose.read_text(encoding="utf-8"))
    documented = example_keys(args.env_example.read_text(encoding="utf-8"))
    missing = sorted(used - documented)
    if missing:
        print(
            f"check_env_example: {args.env_example.name} does not document "
            f"{', '.join(missing)} (used in {args.compose.name})",
            file=sys.stderr,
        )
        return 1
    print(f"check_env_example: all {len(used)} compose variables are documented in {args.env_example.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
