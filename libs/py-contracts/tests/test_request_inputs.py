"""The walker that finds any tenant id declared as a request input (PRD §7.9, §10).

`org_id` comes only from the signed token, so no request model, parameter or nested field may
declare it. These tests prove the walker finds every way one could sneak in.
"""

from typing import Any

import pytest
from onb_contracts import RequestModel, is_tenant_key, request_model_classes, tenant_inputs_in_model
from onb_contracts.inputs import tenant_inputs_in_openapi
from pydantic import BaseModel, Field, ValidationError


@pytest.mark.parametrize(
    "name",
    [
        "org_id",
        "orgId",
        "ORG-ID",
        "organization_id",
        "tenant_id",
        "tenantId",
        "X-Org-Id",
        "parent_org_id",
        "orgid",
        "OrgID",
    ],
)
def test_tenant_keys_are_recognised_in_any_spelling(name: str) -> None:
    assert is_tenant_key(name)


@pytest.mark.parametrize("name", ["org", "organisation_name", "origin_id", "id", "run_id", "org_name", "orgs", ""])
def test_other_keys_are_not_tenant_keys(name: str) -> None:
    assert not is_tenant_key(name)


def test_request_models_forbid_unknown_fields_so_a_sent_org_id_is_rejected() -> None:
    class CreateThing(RequestModel):
        name: str

    with pytest.raises(ValidationError, match="org_id"):
        CreateThing.model_validate({"name": "a", "org_id": "o1"})


def test_a_model_declaring_org_id_is_found() -> None:
    class Bad(BaseModel):
        org_id: str

    assert tenant_inputs_in_model(Bad) == ["Bad.org_id"]


def test_an_alias_spelling_org_id_is_found() -> None:
    class Sneaky(BaseModel):
        owner: str = Field(alias="orgId")

    assert tenant_inputs_in_model(Sneaky) == ["Sneaky.orgId"]


def test_a_nested_org_id_is_found() -> None:
    class Inner(BaseModel):
        org_id: str

    class Outer(BaseModel):
        items: list[Inner]
        lookup: dict[str, Inner]
        maybe: Inner | None = None

    found = tenant_inputs_in_model(Outer)
    assert found
    assert all(path.endswith("org_id") for path in found)


def test_a_clean_model_has_none() -> None:
    class Clean(BaseModel):
        name: str
        run_id: str

    assert tenant_inputs_in_model(Clean) == []


def test_request_model_classes_lists_every_subclass_recursively() -> None:
    class Base(RequestModel):
        a: str

    class Child(Base):
        b: str

    classes = request_model_classes()
    assert Base in classes
    assert Child in classes


def _spec(paths: dict[str, Any], schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"openapi": "3.1.0", "paths": paths, "components": {"schemas": schemas or {}}}


@pytest.mark.parametrize("where", ["query", "path", "header", "cookie"])
def test_a_parameter_named_org_id_is_found(where: str) -> None:
    spec = _spec({"/x": {"get": {"parameters": [{"name": "org_id", "in": where, "schema": {"type": "string"}}]}}})
    assert tenant_inputs_in_openapi(spec) == [f"GET /x {where}:org_id"]


def test_a_request_body_ref_declaring_org_id_is_found_through_nesting() -> None:
    schemas = {
        "Outer": {
            "type": "object",
            "properties": {"inner": {"$ref": "#/components/schemas/Inner"}},
        },
        "Inner": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"anyOf": [{"$ref": "#/components/schemas/Leaf"}]}},
            },
        },
        "Leaf": {"type": "object", "properties": {"org_id": {"type": "string"}}},
    }
    body = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Outer"}}}}
    spec = _spec({"/x": {"post": {"requestBody": body}}}, schemas)
    assert tenant_inputs_in_openapi(spec) == ["POST /x body:inner.items[].org_id"]


def test_org_id_in_additional_properties_and_all_of_is_found() -> None:
    schema = {
        "allOf": [
            {"type": "object", "additionalProperties": {"type": "object", "properties": {"tenantId": {}}}},
        ]
    }
    body = {"content": {"multipart/form-data": {"schema": schema}}}
    spec = _spec({"/x": {"put": {"requestBody": body}}})
    assert tenant_inputs_in_openapi(spec) == ["PUT /x body:{}.tenantId"]


def test_org_id_in_responses_is_allowed() -> None:
    # Responses may echo the caller's org; only inputs are forbidden.
    schemas = {"Out": {"type": "object", "properties": {"org_id": {"type": "string"}}}}
    responses = {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Out"}}}}}
    spec = _spec({"/x": {"get": {"responses": responses}}}, schemas)
    assert tenant_inputs_in_openapi(spec) == []


def test_recursive_schemas_terminate() -> None:
    schemas = {"Node": {"type": "object", "properties": {"child": {"$ref": "#/components/schemas/Node"}}}}
    body = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}
    spec = _spec({"/x": {"post": {"requestBody": body}}}, schemas)
    assert tenant_inputs_in_openapi(spec) == []


def test_path_level_parameters_are_checked() -> None:
    spec = _spec({"/orgs/{org_id}": {"parameters": [{"name": "org_id", "in": "path"}], "get": {}}})
    assert tenant_inputs_in_openapi(spec) == ["* /orgs/{org_id} path:org_id"]


def test_a_ref_outside_the_components_fails_loudly() -> None:
    body = {"content": {"application/json": {"schema": {"$ref": "https://example.test/other.json"}}}}
    spec = _spec({"/x": {"post": {"requestBody": body}}})
    with pytest.raises(KeyError, match="unsupported"):
        tenant_inputs_in_openapi(spec)


def test_an_unresolvable_ref_fails_loudly() -> None:
    body = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}}}
    spec = _spec({"/x": {"post": {"requestBody": body}}})
    with pytest.raises(KeyError, match="Missing"):
        tenant_inputs_in_openapi(spec)
