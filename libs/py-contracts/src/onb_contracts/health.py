"""`GET /health` answer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from onb_contracts.envelope import Envelope


class Health(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]
    service: str


class HealthEnvelope(Envelope[Health]):
    """The API is up."""
