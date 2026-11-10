"""`org_id` is never a request input (PRD §7.9 Identity, §10).

The tenant comes only from the signed token. `RequestModel` is the base for request bodies and
forbids unknown fields, so a client sending `org_id` gets a validation error. The walkers here find
any tenant id a model or an OpenAPI document declares as an input; tests run them over every request
model and the API's whole OpenAPI document.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

_TENANT_WORDS = frozenset(
    {"org", "orgs", "organization", "organizations", "organisation", "organisations", "tenant", "tenants"}
)
_ID_WORDS = frozenset({"id", "ids", "uuid", "key", "slug"})
_TENANT_IDS = frozenset(f"{t}{i}" for t in _TENANT_WORDS for i in _ID_WORDS)
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
_REF_PREFIX = "#/components/schemas/"


class RequestModel(BaseModel):
    """Base for every request body: frozen, and unknown fields (such as `org_id`) are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def is_tenant_key(name: str) -> bool:
    """True for a name that carries the tenant, in any spelling.

    A tenant word (`org`, `tenant`, `organization`, plurals) alone, or followed by an id word (`id`,
    `ids`, `uuid`, `key`, `slug`) anywhere in the name: `org`, `X-Tenant`, `orgId`, `ORG-ID`,
    `parent_org_id`, `tenant_slug` all count; `org_name`, `origin_id`, `organisation_name` do not.
    Words are split on case changes and separators; a header's leading `x` is ignored.
    """
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]
    if len(words) > 1 and words[0] == "x":
        words = words[1:]
    if len(words) == 1 and (words[0] in _TENANT_WORDS or words[0] in _TENANT_IDS):
        return True
    return any(f"{a}{b}" in _TENANT_IDS for a, b in itertools.pairwise(words))


def request_model_classes() -> list[type[RequestModel]]:
    """Every subclass of `RequestModel` defined so far, at any depth."""
    found: list[type[RequestModel]] = []
    pending = list(RequestModel.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls not in found:
            found.append(cls)
            pending.extend(cls.__subclasses__())
    return found


def tenant_inputs_in_model(model: type[BaseModel]) -> list[str]:
    """Paths of every tenant key `model` declares, at any depth, as `Model.path.to.key`."""
    return tenant_inputs_in_type(model, model.__name__)


def tenant_inputs_in_type(tp: Any, label: str) -> list[str]:
    """Like `tenant_inputs_in_model` for any type Pydantic can validate (dataclass, TypedDict, list[...]).

    Both the wire names (aliases) and the Python names are checked, so `org_id: str = Field(alias="o")`
    is still found.
    """
    found: list[str] = []
    for by_alias in (True, False):
        schema = TypeAdapter(tp).json_schema(by_alias=by_alias, ref_template=_REF_PREFIX + "{model}")
        components = {"schemas": schema.get("$defs", {})}
        for path in _walk_schema(schema, components, "", frozenset()):
            hit = f"{label}.{path}"
            if hit not in found:
                found.append(hit)
    return found


def tenant_inputs_in_openapi(spec: Mapping[str, Any]) -> list[str]:
    """Every parameter or request-body field in `spec` that is a tenant key, as `METHOD /path where:name`."""
    components = spec.get("components", {})
    found: list[str] = []
    for path, item in spec.get("paths", {}).items():
        found.extend(_param_hits("*", path, item.get("parameters", ())))
        for method in _HTTP_METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            label = method.upper()
            found.extend(_param_hits(label, path, operation.get("parameters", ())))
            for media in operation.get("requestBody", {}).get("content", {}).values():
                for hit in _walk_schema(media.get("schema", {}), components, "", frozenset()):
                    found.append(f"{label} {path} body:{hit}")
    return found


def _param_hits(label: str, path: str, params: Any) -> Iterator[str]:
    for param in params:
        if is_tenant_key(str(param.get("name", ""))):
            yield f"{label} {path} {param.get('in', '?')}:{param['name']}"


def _resolve(ref: str, components: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if not ref.startswith(_REF_PREFIX):
        raise KeyError(f"unsupported $ref {ref!r}")
    name = ref.removeprefix(_REF_PREFIX)
    schemas = components.get("schemas", {})
    if name not in schemas:
        raise KeyError(f"$ref to a schema that does not exist: {name}")
    return name, schemas[name]


def _walk_schema(
    schema: Mapping[str, Any], components: Mapping[str, Any], prefix: str, seen: frozenset[str]
) -> Iterator[str]:
    if "$ref" in schema:
        name, target = _resolve(schema["$ref"], components)
        if name not in seen:
            yield from _walk_schema(target, components, prefix, seen | {name})
        return
    for key, sub in schema.get("properties", {}).items():
        path = f"{prefix}{key}"
        if is_tenant_key(key):
            yield path
        yield from _walk_schema(sub, components, f"{path}.", seen)
    for key in _SUBSCHEMA_LISTS:
        for sub in schema.get(key, ()):
            yield from _walk_schema(sub, components, prefix, seen)
    items = schema.get("items")
    if isinstance(items, Mapping):
        yield from _walk_schema(items, components, f"{prefix.removesuffix('.')}[].", seen)
    extra = schema.get("additionalProperties")
    if isinstance(extra, Mapping):
        yield from _walk_schema(extra, components, f"{prefix}{{}}.", seen)
