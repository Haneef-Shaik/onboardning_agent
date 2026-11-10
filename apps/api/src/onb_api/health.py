"""`GET /health`: liveness, in the envelope like every other answer."""

from __future__ import annotations

from fastapi import APIRouter
from onb_contracts import Envelope
from onb_contracts.health import Health, HealthEnvelope

from onb_api.responses import respond

SERVICE_NAME = "api"

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthEnvelope, operation_id="getHealth")
async def health() -> Envelope[Health]:
    return respond(Health(status="ok", service=SERVICE_NAME))
