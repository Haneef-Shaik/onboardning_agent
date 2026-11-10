"""Seed token names ↔ issued vault tokens.

The seed names its tokens readably (`ptk_seed_acme001_mobile`); the vault issues random ones. Cases and
their checks are written with seed names (`regex_absent: "ptk_seed_acme001_(pan|bankacct)"`), so the
harness rewrites seed names to issued tokens on the way in (fixture documents, case input, scripted
turns) and issued tokens back to seed names on the way out (captures and database reads), before any
check runs. A token the fixture did not issue stays as it is, and checks treat it as unknown.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = ["TokenMap"]


def _pattern(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    return re.compile("|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)))


class TokenMap:
    __slots__ = ("_real_pattern", "_real_to_seed", "_seed_pattern", "_seed_to_real")

    def __init__(self, seed_to_real: Mapping[str, str]) -> None:
        self._seed_to_real = dict(seed_to_real)
        self._real_to_seed = {real: seed for seed, real in self._seed_to_real.items()}
        self._seed_pattern = _pattern(list(self._seed_to_real))
        self._real_pattern = _pattern(list(self._real_to_seed))

    def real_for(self, seed_name: str) -> str | None:
        return self._seed_to_real.get(seed_name)

    def to_real(self, value: Any) -> Any:
        """A copy of `value` with every seed name in its strings replaced by the issued token."""
        return _rewrite(value, self._seed_pattern, self._seed_to_real)

    def to_seed(self, value: Any) -> Any:
        """A copy of `value` with every issued token in its strings replaced by its seed name."""
        return _rewrite(value, self._real_pattern, self._real_to_seed)


def _rewrite(value: Any, pattern: re.Pattern[str] | None, table: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return value if pattern is None else pattern.sub(lambda m: table[m.group(0)], value)
    if isinstance(value, Mapping):
        return {key: _rewrite(item, pattern, table) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_rewrite(item, pattern, table) for item in value]
    return value
