"""The check registry and the one place a check is run.

A check is an async function `(params, context) -> CheckResult`, registered under its catalog name.
`run_check` applies `when_present` and turns an exception inside a check into a failure, so a broken
output can never make a case pass.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeVar

from onb_evals.checks.params import PARAMS, CheckParams
from onb_evals.context import CheckContext
from onb_evals.paths import MISSING, resolve

if TYPE_CHECKING:
    from onb_evals.cases import CheckSpec

__all__ = ["CheckResult", "register", "registered", "run_check"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    detail: str = ""


P = TypeVar("P", bound=CheckParams)
CheckFn = Callable[[Any, CheckContext], Awaitable[CheckResult]]


@dataclass(frozen=True, slots=True)
class _CheckDef:
    fn: CheckFn
    presence_path: str | None


_REGISTRY: Final[dict[str, _CheckDef]] = {}


def register(name: str, *, presence_path: str | None = None) -> Callable[[CheckFn], CheckFn]:
    """Register a check. `presence_path` is what `when_present` looks for when the params name no `path`."""
    if name not in PARAMS:
        raise ValueError(f"{name!r} is not in the check catalog")

    def decorate(fn: CheckFn) -> CheckFn:
        if name in _REGISTRY:
            raise ValueError(f"check {name!r} is registered twice")
        _REGISTRY[name] = _CheckDef(fn, presence_path)
        return fn

    return decorate


def registered() -> frozenset[str]:
    return frozenset(_REGISTRY)


def passed(detail: str = "") -> CheckResult:
    return CheckResult(True, detail)


def failed(detail: str) -> CheckResult:
    return CheckResult(False, detail)


def _absent(spec: CheckSpec, definition: _CheckDef, context: CheckContext) -> bool:
    path = getattr(spec.params, "path", None) or definition.presence_path
    return path is not None and resolve(context.output, path) is MISSING


async def run_check(spec: CheckSpec, context: CheckContext) -> CheckResult:
    definition = _REGISTRY.get(spec.type)
    if definition is None:
        return failed(f"no implementation for check {spec.type!r}")
    if spec.params.when_present and _absent(spec, definition, context):
        return passed("not present (when_present)")
    try:
        return await definition.fn(spec.params, context)
    except Exception as error:  # a check that cannot read the output fails the case, never passes it
        return failed(f"check raised {type(error).__name__}")
