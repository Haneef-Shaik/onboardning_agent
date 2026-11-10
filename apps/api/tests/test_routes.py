"""Route-wide rules: every route answers in the envelope, and no request declares `org_id` (PRD §7.9, §10)."""

import typing
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_contracts import Envelope, is_tenant_key, request_model_classes, tenant_inputs_in_model
from onb_contracts.inputs import tenant_inputs_in_openapi
from pydantic import BaseModel


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """Every API route, including those in included routers (FastAPI 0.16x nests them, unflattened).

    The route's own dependant is walked; dependencies added at `include_router(dependencies=...)`
    are covered by the OpenAPI walk, which sees the effective routes.
    """
    contexts = iter_route_contexts(app.routes)
    return [ctx.original_route for ctx in contexts if isinstance(ctx.original_route, APIRoute)]


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in typing.get_args(annotation) for m in _models_in(arg)]


def _dependants(root: Dependant) -> Iterator[Dependant]:
    """The route's own dependant and every sub-dependency, at any depth."""
    pending = [root]
    while pending:
        dependant = pending.pop()
        yield dependant
        pending.extend(dependant.dependencies)


def _tenant_inputs_in_routes(app: FastAPI) -> tuple[int, list[str]]:
    """Walk every route's declared inputs, through its dependencies (including routes hidden from OpenAPI)."""
    found: list[str] = []
    routes = _api_routes(app)
    for route in routes:
        for dependant in _dependants(route.dependant):
            params = [*dependant.path_params, *dependant.query_params, *dependant.header_params]
            for param in [*params, *dependant.cookie_params]:
                found.extend(f"{route.path} param:{name}" for name in {param.name, param.alias} if is_tenant_key(name))
                for model in _models_in(param.field_info.annotation):
                    found.extend(f"{route.path} {hit}" for hit in tenant_inputs_in_model(model))
            for body in dependant.body_params:
                found.extend(f"{route.path} body:{name}" for name in {body.name, body.alias} if is_tenant_key(name))
                for model in _models_in(body.field_info.annotation):
                    found.extend(f"{route.path} {hit}" for hit in tenant_inputs_in_model(model))
    return len(routes), found


def test_no_request_model_or_parameter_declares_org_id() -> None:
    app = create_app()

    walked, route_hits = _tenant_inputs_in_routes(app)
    openapi_hits = tenant_inputs_in_openapi(app.openapi())
    model_hits = [hit for model in request_model_classes() for hit in tenant_inputs_in_model(model)]

    assert walked >= 1, "the walk saw no routes, so it proved nothing"
    assert route_hits == [], f"routes declare org_id as an input: {route_hits}"
    assert openapi_hits == [], f"the OpenAPI document declares org_id as an input: {openapi_hits}"
    assert model_hits == [], f"request models declare org_id: {model_hits}"


def test_the_route_walk_finds_org_id_however_it_is_declared() -> None:
    class Inner(BaseModel):
        org_id: str

    class Body(BaseModel):
        inner: Inner

    app = create_app()

    async def by_query(org_id: str) -> None:
        return None

    async def by_body(body: Body) -> None:
        return None

    async def by_path(org_id: str) -> None:
        return None

    async def org_from_header(x_org_id: Annotated[str, Header()]) -> str:
        return x_org_id

    async def by_dependency(org: Annotated[str, Depends(org_from_header)]) -> None:
        return None

    app.add_api_route("/q", by_query, include_in_schema=False)
    app.add_api_route("/b", by_body, methods=["POST"])
    app.add_api_route("/p/{org_id}", by_path)
    app.add_api_route("/d", by_dependency)

    _, hits = _tenant_inputs_in_routes(app)
    assert "/q param:org_id" in hits
    assert "/b Body.inner.org_id" in hits
    assert "/p/{org_id} param:org_id" in hits
    assert "/d param:x-org-id" in hits


def test_every_route_declares_an_envelope_response_model() -> None:
    app = create_app()
    for route in _api_routes(app):
        model = route.response_model
        assert isinstance(model, type), f"{route.path} has no response model"
        assert issubclass(model, Envelope), f"{route.path} answers outside the envelope"


def test_every_operation_documents_the_error_envelope() -> None:
    spec = create_app().openapi()
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            default = operation["responses"].get("default", {})
            schema = default.get("content", {}).get("application/json", {}).get("schema", {})
            assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}, f"{method} {path}"


def test_health_answers_in_the_envelope(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] == {"status": "ok", "service": "api"}
    assert set(body["meta"]) == {"trace_id", "page"}
