"""Export the API's OpenAPI document: `python -m onb_api.openapi <out.json>` (PRD §11 Contracts).

The output is deterministic (sorted keys, two-space indent, trailing newline), so the committed copy
in `libs/ts-api-client/openapi.json` only changes when the API's contract does. `make contracts`
regenerates it and the TS client; `make contracts-check` fails when either is stale.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from onb_api.app import create_app
from onb_api.auth.settings import AuthSettings


def export_openapi() -> str:
    # The document does not depend on the secret; a throw-away one lets the export run without config.
    spec = create_app(auth=AuthSettings.ephemeral()).openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m onb_api.openapi <out.json>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(export_openapi(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
