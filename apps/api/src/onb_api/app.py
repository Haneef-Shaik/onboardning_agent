"""`create_app()`: the API with the envelope, the error handling and every router (PRD §10).

It does not start telemetry: `onb_api.main` does that before building the app, so tests and the
OpenAPI export can build apps freely.

Auth settings default to the environment, so an app built without them fails at once when
`JWT_SECRET` is missing (PRD §7.8). Without a user store, login and `/me` answer 503; without an audit
log, `GET /audit` answers 503; without an agent registry, `GET /agents` answers 503; without a hire
service, the hire and PII routes answer 503; without a template lifecycle, `/templates` answers 503;
without a document service, the document upload answers 503; without an eval run store,
`/evals/runs*` answer 503 (UI-8); without mock channels, `GET /mock-outbox` and
`POST /webhooks/telegram` answer 503 (NOTIF-1); without a run service, `POST /runs` answers 503
(RUN-1); without a chat service, `/runs/{id}/chat*` answer 503 (CHAT-1); without an LLM
configuration, agent records carry no resolved model. On startup the app
makes the user store's, the audit log's, the hire service's (hires, vault, org keys, PII access
log), the template store's, the document service's (encrypted bucket, `documents_meta`), the eval
runs', the mock channels' and the run service's (runs, run tasks, the outbox) indexes and the
stand-in password hash.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from onb_contracts import ErrorEnvelope
from onb_llm import LLMConfig
from onb_telemetry import active_telemetry, instrument_app
from starlette.concurrency import run_in_threadpool

from onb_api import health
from onb_api.agents import routes as agent_routes
from onb_api.agents.reader import AgentRegistryReader
from onb_api.audit import routes as audit_routes
from onb_api.audit.reader import AuditReader
from onb_api.auth import routes as auth_routes
from onb_api.auth.passwords import warm_up
from onb_api.auth.settings import AuthSettings
from onb_api.auth.users import UserStore
from onb_api.body_limit import BodyLimitMiddleware
from onb_api.chat import ChatService
from onb_api.chat import routes as chat_routes
from onb_api.documents import routes as document_routes
from onb_api.documents.service import DocumentService
from onb_api.errors import install_error_handling
from onb_api.evals import routes as eval_routes
from onb_api.evals.store import EvalRunsReader
from onb_api.hires import routes as hire_routes
from onb_api.hires.service import HireService
from onb_api.notify import routes as notify_routes
from onb_api.notify.service import MockChannels
from onb_api.runs import RunService
from onb_api.runs import routes as run_routes
from onb_api.templates import TemplateLifecycle
from onb_api.templates import routes as template_routes

TITLE = "Onboarding Platform API"
VERSION = "0.1.0"

# Every operation documents the error envelope, so the TS client types the error branch too.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {"model": ErrorEnvelope, "description": "Error, in the envelope"},
}


def create_app(
    *,
    auth: AuthSettings | None = None,
    users: UserStore | None = None,
    audit: AuditReader | None = None,
    agents: AgentRegistryReader | None = None,
    hires: HireService | None = None,
    templates: TemplateLifecycle | None = None,
    documents: DocumentService | None = None,
    evals: EvalRunsReader | None = None,
    llm: LLMConfig | None = None,
    notify: MockChannels | None = None,
    runs: RunService | None = None,
    chat: ChatService | None = None,
) -> FastAPI:
    settings = auth if auth is not None else AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if users is not None:
            await users.ensure_indexes()
        if audit is not None:
            await audit.ensure_indexes()
        if hires is not None:
            await hires.ensure_indexes()
        if templates is not None:
            await templates.ensure_indexes()
        if documents is not None:
            await documents.ensure_indexes()
        if evals is not None:
            await evals.ensure_indexes()
        if notify is not None:
            await notify.ensure_indexes()
        if runs is not None:
            await runs.ensure_indexes()
        if chat is not None:
            await chat.ensure_indexes()
        await run_in_threadpool(warm_up, settings.bcrypt_rounds)
        yield

    app = FastAPI(title=TITLE, version=VERSION, responses=_ERROR_RESPONSES, lifespan=lifespan)
    app.state.auth = settings
    app.state.users = users
    app.state.audit = audit
    app.state.agents = agents
    app.state.hires = hires
    app.state.templates = templates
    app.state.documents = documents
    app.state.evals = evals
    app.state.llm = llm
    app.state.notify = notify
    app.state.runs = runs
    app.state.chat = chat
    install_error_handling(app)
    # Added after the error handling, so it sits outside it: an oversized body never reaches a route.
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(agent_routes.router)
    app.include_router(hire_routes.router)
    app.include_router(template_routes.router)
    app.include_router(document_routes.router)
    app.include_router(eval_routes.router)
    app.include_router(notify_routes.router)
    app.include_router(run_routes.router)
    app.include_router(chat_routes.router)
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
