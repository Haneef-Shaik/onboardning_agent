"""What a check can see: the case, its capture, the database after the run, the vault and fixture facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from onb_evals.capture import CaseCapture
    from onb_evals.cases import EvalCase

__all__ = ["CheckContext", "FixtureFacts", "StateReader", "VaultProbe"]

Document = dict[str, Any]


class StateReader(Protocol):
    """Reads the case's throwaway database, scoped to one org, after the run."""

    async def find(self, collection: str, query: Mapping[str, Any], *, org_id: str) -> list[Document]: ...


class VaultProbe(Protocol):
    """Asks the vault about a token as the checks see it (seed names, see `onb_evals.tokens`)."""

    def owner(self, token: str) -> tuple[str, str, str] | None:
        """`(org_id, subject_id, field)` of a token the fixture seeded; `None` for any other token."""
        ...

    async def is_bound(self, token: str, *, org_id: str, subject_id: str, field: str) -> bool:
        """The vault holds a verified record for `token` bound to this org, subject and field."""
        ...


@dataclass(frozen=True, slots=True)
class FixtureFacts:
    """Read-only facts from the seed that checks compare against."""

    pii_values: tuple[str, ...]
    action_agents: Mapping[str, str]
    approval_actions: frozenset[str]
    templates: Mapping[tuple[str, str, int], Mapping[str, Any]]
    agent_tools: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckContext:
    case: EvalCase
    capture: CaseCapture
    state: StateReader
    vault: VaultProbe
    facts: FixtureFacts
    contracts: Mapping[str, tuple[str, type[BaseModel]]]

    @property
    def output(self) -> Mapping[str, Any] | None:
        return self.capture.output
