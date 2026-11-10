"""`${VAR}` and `${VAR:-default}` substitution for `llm.yaml` values.

`${VAR}` is required: unset or blank fails, naming the variable and the config key. `${VAR:-default}`
takes `default` when the variable is unset or blank, as in a POSIX shell.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from onb_llm.errors import LLMConfigError

_PLACEHOLDER = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def interpolate(text: str, env: Mapping[str, str], *, where: str) -> str:
    """`text` with every placeholder replaced from `env`; `where` names the key in error messages."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group("name")
        value = env.get(name, "")
        if value.strip():
            return value
        default = match.group("default")
        if default is not None:
            return default
        raise LLMConfigError(f"{where} needs environment variable {name}, which is not set (see .env.example)")

    return _PLACEHOLDER.sub(substitute, text)


def interpolate_tree(value: Any, env: Mapping[str, str], *, where: str) -> Any:
    """A copy of a parsed YAML tree with every string interpolated. The input is not changed."""
    if isinstance(value, str):
        return interpolate(value, env, where=where)
    if isinstance(value, Mapping):
        return {
            key: interpolate_tree(item, env, where=f"{where}.{key}" if where else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [interpolate_tree(item, env, where=f"{where}[{index}]") for index, item in enumerate(value)]
    return value
