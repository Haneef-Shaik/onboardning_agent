"""`create_app()`: the API with the envelope, the error handling and every router (PRD §10).

It does not start telemetry: `onb_api.main` does that before building the app, so tests and the
OpenAPI export can build apps freely.

Auth settings default to the environment, so an app built without them fails at once when
`JWT_SECRET` is missing (PRD §7.8). Without a user store, login and `/me` answer 503.
"""

from __future__ import annotations

from fastapi import FastAPI
from onb_contracts import ErrorEnvelope
from onb_telemetry import active_telemetry, instrument_app

from onb_api import health
from onb_api.auth import routes as auth_routes
from onb_api.auth.settings import AuthSettings
from onb_api.auth.users import UserStore
from onb_api.errors import install_error_handling

TITLE = "Onboarding Platform API"
VERSION = "0.1.0"

# Every operation documents the error envelope, so the TS client types the error branch too.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {"model": ErrorEnvelope, "description": "Error, in the envelope"},
}


def create_app(*, auth: AuthSettings | None = None, users: UserStore | None = None) -> FastAPI:
    settings = auth if auth is not None else AuthSettings.from_env()
    app = FastAPI(title=TITLE, version=VERSION, responses=_ERROR_RESPONSES)
    app.state.auth = settings
    app.state.users = users
    install_error_handling(app)
    app.include_router(health.router)
    app.include_router(auth_routes.router)
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
