"""`create_app()`: the API with the envelope, the error handling and every router (PRD §10).

It does not start telemetry: `onb_api.main` does that before building the app, so tests and the
OpenAPI export can build apps freely.

Auth settings default to the environment, so an app built without them fails at once when
`JWT_SECRET` is missing (PRD §7.8). Without a user store, login and `/me` answer 503; without an audit
log, `GET /audit` answers 503. On startup the app makes the user store's and the audit log's indexes
and the stand-in password hash.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from onb_contracts import ErrorEnvelope
from onb_telemetry import active_telemetry, instrument_app
from starlette.concurrency import run_in_threadpool

from onb_api import health
from onb_api.audit import routes as audit_routes
from onb_api.audit.reader import AuditReader
from onb_api.auth import routes as auth_routes
from onb_api.auth.passwords import warm_up
from onb_api.auth.settings import AuthSettings
from onb_api.auth.users import UserStore
from onb_api.errors import install_error_handling

TITLE = "Onboarding Platform API"
VERSION = "0.1.0"

# Every operation documents the error envelope, so the TS client types the error branch too.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {"model": ErrorEnvelope, "description": "Error, in the envelope"},
}


def create_app(
    *, auth: AuthSettings | None = None, users: UserStore | None = None, audit: AuditReader | None = None
) -> FastAPI:
    settings = auth if auth is not None else AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if users is not None:
            await users.ensure_indexes()
        if audit is not None:
            await audit.ensure_indexes()
        await run_in_threadpool(warm_up, settings.bcrypt_rounds)
        yield

    app = FastAPI(title=TITLE, version=VERSION, responses=_ERROR_RESPONSES, lifespan=lifespan)
    app.state.auth = settings
    app.state.users = users
    app.state.audit = audit
    install_error_handling(app)
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(audit_routes.router)
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
