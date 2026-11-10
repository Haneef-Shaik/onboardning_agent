"""Generate or check the API contract artefacts (C-1, PRD §11 Contracts: Pydantic → OpenAPI → TS client).

    python scripts/contracts.py generate   # `make contracts`: rewrite both files from the API
    python scripts/contracts.py check      # `make contracts-check`: fail if either file is stale

The two committed files are `libs/ts-api-client/openapi.json` (from `onb_api.openapi`) and
`libs/ts-api-client/src/generated/schema.ts` (from openapi-typescript). `check` renders both into a
temporary directory and compares them byte for byte with the committed copies.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from onb_api.openapi import export_openapi

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "libs" / "ts-api-client" / "openapi.json"
SCHEMA = ROOT / "libs" / "ts-api-client" / "src" / "generated" / "schema.ts"
GENERATOR = ROOT / "node_modules" / ".bin" / "openapi-typescript"
DIFF_LINES = 60
USAGE = "usage: python scripts/contracts.py generate|check"


def _render(workdir: Path) -> tuple[str, str]:
    """The OpenAPI document and the TS schema the current API produces."""
    if not GENERATOR.exists():
        raise FileNotFoundError(f"{GENERATOR} is missing: run `npm ci` (or `make bootstrap`)")
    openapi = workdir / "openapi.json"
    schema = workdir / "schema.ts"
    openapi.write_text(export_openapi(), encoding="utf-8")
    subprocess.run(  # noqa: S603  # fixed argv: the pinned generator from node_modules
        [str(GENERATOR), str(openapi), "--output", str(schema)],
        check=True,
        capture_output=True,
        cwd=ROOT,
    )
    return openapi.read_text(encoding="utf-8"), schema.read_text(encoding="utf-8")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _expected() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="onb-contracts-") as tmp:
        return _render(Path(tmp))


def drifted(openapi_path: Path, schema_path: Path) -> list[Path]:
    """The committed files that differ from what the API generates now (a missing file differs)."""
    return [path for path, _ in _differences(openapi_path, schema_path)]


def _differences(openapi_path: Path, schema_path: Path) -> list[tuple[Path, str]]:
    openapi, schema = _expected()
    pairs = ((openapi_path, openapi), (schema_path, schema))
    return [(path, want) for path, want in pairs if _read(path) != want]


def generate(openapi_path: Path = OPENAPI, schema_path: Path = SCHEMA) -> None:
    openapi, schema = _expected()
    for path, text in ((openapi_path, openapi), (schema_path, schema)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def check(openapi_path: Path = OPENAPI, schema_path: Path = SCHEMA) -> int:
    stale = _differences(openapi_path, schema_path)
    if not stale:
        print("contracts-check: openapi.json and the TS client are up to date")
        return 0
    for path, want in stale:
        have = (_read(path) or "").splitlines(keepends=True)
        diff = list(difflib.unified_diff(have, want.splitlines(keepends=True), f"{path} (committed)", f"{path} (API)"))
        print("".join(diff[:DIFF_LINES]), file=sys.stderr, end="")
        if len(diff) > DIFF_LINES:
            print(f"... {len(diff) - DIFF_LINES} more diff lines", file=sys.stderr)
    names = ", ".join(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path) for path, _ in stale)
    print(f"contracts-check: stale: {names}. Run `make contracts` and commit the result.", file=sys.stderr)
    return 1


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["generate"]:
        generate()
        print(f"contracts: wrote {OPENAPI.relative_to(ROOT)} and {SCHEMA.relative_to(ROOT)}")
        return 0
    if list(argv) == ["check"]:
        return check()
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
