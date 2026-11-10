"""`python -m onb_llm.health --connector bedrock`: call every alias that connector serves, once.

Run by `make llm-health CONNECTOR=…`, which sets `LLM_CONNECTOR` too. Exit codes: 0 every alias
answered, 1 at least one did not, 2 the configuration or environment is wrong (nothing was called).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from onb_llm.config import LLMSelection, load_llm_config
from onb_llm.errors import LLMError
from onb_llm.registry import discover_connectors
from onb_llm.runtime import LLMRuntime

DEFAULT_CONFIG = Path("config/llm.yaml")


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onb_llm.health", description=__doc__)
    parser.add_argument("--connector", required=True, help="connector whose aliases to check")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to llm.yaml")
    args = parser.parse_args(argv)
    environment = dict(os.environ if env is None else env)
    try:
        runtime = LLMRuntime.start(load_llm_config(args.config, environment), discover_connectors(), environment)
    except LLMError as error:
        print(f"llm-health: {error}", file=sys.stderr)
        return 2
    aliases = {name: s for name, s in runtime.config.aliases.items() if s.connector == args.connector}
    if not aliases:
        print(f"llm-health: no alias uses connector '{args.connector}' in {args.config}", file=sys.stderr)
        return 2
    healthy = 0
    for alias in aliases:
        resolved = runtime.resolve(org_override=LLMSelection(alias=alias))
        status = runtime.connector(resolved.connector).healthcheck(resolved.model, resolved.params)
        outcome = f"ok latency_ms={status.latency_ms}" if status.ok else f"FAILED {status.detail}"
        print(f"alias={alias} connector={resolved.connector} model={resolved.model} {outcome}")
        healthy += status.ok
    print(f"{healthy} of {len(aliases)} healthy")
    return 0 if healthy == len(aliases) else 1


if __name__ == "__main__":
    sys.exit(main())
