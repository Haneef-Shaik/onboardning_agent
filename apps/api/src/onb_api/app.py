"""`create_app()`: the API with the envelope, the error handling and every router (PRD §10).

It does not start telemetry: `onb_api.main` does that before building the app, so tests and the
OpenAPI export can build apps freely.
"""

from __future__ import annotations

from fastapi import FastAPI
from onb_contracts import ErrorEnvelope
from onb_telemetry import active_telemetry, instrument_app

from onb_api import health
from onb_api.errors import install_error_handling

TITLE = "Onboarding Platform API"
VERSION = "0.1.0"

# Every operation documents the error envelope, so the TS client types the error branch too.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {"model": ErrorEnvelope, "description": "Error, in the envelope"},
}


def create_app() -> FastAPI:
    app = FastAPI(title=TITLE, version=VERSION, responses=_ERROR_RESPONSES)
    install_error_handling(app)
    app.include_router(health.router)
    if telemetry_is_active():
        # `from fastapi import FastAPI` above bound the class before the instrumentor could swap
        # it, so instrument this app explicitly.
        instrument_app(app)
    return app


def telemetry_is_active() -> bool:
    try:
        active_telemetry()
    except RuntimeError:
        return False
    return True
