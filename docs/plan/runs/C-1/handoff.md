# Handoff — C-1 · Stand up the API with one envelope and a generated TS client        attempt 1 · goal/c-1-api-contracts · evidence at a4b775a (see `git log -1` for the handoff commit)

**Outcome claimed.** `apps/api` (FastAPI) answers every request, success or error, in the `{success, data, error, meta}` envelope from `libs/py-contracts`. `libs/ts-api-client` is generated from the API's OpenAPI by `make contracts`, and `make contracts-check` fails when either generated file is stale.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A test walks every request model and fails if one declares `org_id` (seen to fail once) | yes | `apps/api/tests/test_routes.py::test_no_request_model_or_parameter_declares_org_id` PASSED (`uv run --frozen pytest apps/api/tests/test_routes.py -v`; `docs/evidence/C-1/box1-box4-tests.txt`). It walks three things. (1) Every route, through included routers (`iter_route_contexts`) and its dependency tree: path, query, header and cookie params, plus body params, with each param's type walked by `tenant_inputs_in_type` (models, dataclasses, TypedDicts, nesting), including routes hidden from OpenAPI. (2) The whole OpenAPI document (`tenant_inputs_in_openapi`: params, request bodies, `$ref`s, `allOf/anyOf/oneOf`, `items`, `additionalProperties`). (3) Every `RequestModel` subclass, by wire names and by Python names. It asserts it saw ≥ 1 route. **Seen to fail:** `docs/evidence/C-1/mutation-checks.txt` M1. `GET /health` gains `org_id: str \| None = None`, and the result is `FAILED apps/api/tests/test_routes.py::test_no_request_model_or_parameter_declares_org_id`. M2 (a `RequestModel` subclass with `org_id`) fails it too. M3 and M15 break the matcher or the alias check, and the walker tests fail. The walker itself is proved by `test_the_route_walk_finds_org_id_however_it_is_declared` (query on a hidden route, nested body, path, header via a dependency, dataclass body on a hidden route) and by `libs/py-contracts/tests/test_request_inputs.py`. Extras: `RequestModel` is `extra="forbid"`, so a sent `org_id` gets 422 (`test_request_models_forbid_unknown_fields_so_a_sent_org_id_is_rejected`). `test_every_request_model_rejects_unknown_fields` keeps it that way. |
| `make contracts-check` fails after a model change without regeneration and passes after it (both outputs) | yes | `docs/evidence/C-1/box2-contracts-check.txt`, produced by `uv run --frozen python docs/evidence/C-1/box2_contracts_drift.py`. (1) `Health` gains `version: str`. `make contracts-check` prints a unified diff of `openapi.json` and `schema.ts`, then `contracts-check: stale: libs/ts-api-client/openapi.json, libs/ts-api-client/src/generated/schema.ts. Run \`make contracts\` …`, `exit=2`. (2) `make contracts` gives `exit=0`, then `make contracts-check` prints `… up to date`, `exit=0`. (3) The script restores the files byte for byte (`restored byte-for-byte: True`), and the check stays green. The current tree: `docs/evidence/C-1/make-contracts-check.txt` (`exit=0`). Logic tests are in `tools/tests/test_contracts.py` (13 passed): stale openapi, stale TS, a missing file, a missing or failing generator (fails closed), and the committed files match the API. M10 (comparison removed) fails 4 of them. |
| `apps/web` imports `ts-api-client` and typechecks | yes | `apps/web/src/api.ts` imports `createApiClient`, `unwrap`, `ApiClient` and `Health` from `@onb/ts-api-client`. `docs/evidence/C-1/box3-web-typecheck.txt` shows `nx run web:typecheck --skip-nx-cache` giving `Successfully ran target typecheck for project web`, `exit=0`, and `web:test` giving `Tests 4 passed (4)`. The types are the generated ones, not `any`. `apps/web/src/api.test.ts` has `// @ts-expect-error` on `client.GET("/no-such-route")`, and `tsc` would fail on an unused expect-error. The Nx graph shows the `web → ts-api-client` dependency. |
| An unhandled exception returns the envelope with a stable error code and no stack trace (test) | yes | `apps/api/tests/test_errors.py::test_unhandled_exception_returns_the_envelope_with_a_stable_code_and_no_stack_trace[/test/boom]` and `[/test/boom-sync]` PASSED (`box1-box4-tests.txt`). The response is 500, `application/json`, with exactly the 4 keys, `success=false`, `data=null`, `error.code="INTERNAL_ERROR"` and the fixed message "Internal server error". It has no `Traceback`, exception type, message, `File "` or `.py`. The TestClient runs with `raise_server_exceptions=True`, so nothing escapes the app. Related tests: `test_unhandled_exception_log_names_the_type_but_not_the_message`, `test_an_unhandled_error_marks_the_server_span_as_failed_without_the_message` and `test_a_failure_after_the_response_started_is_re_raised_without_its_message`. Mutations M4, M5, M6, M12 and M14 make them fail. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Every response model is a named subclass: `class XEnvelope(Envelope[X])` in `onb_contracts`, used as `response_model=XEnvelope`. Return `respond(data)` (or `respond(data, page=PageMeta(...))`); it fills `meta.trace_id`. | `test_every_route_declares_an_envelope_response_model`, `test_schema_names_are_the_readable_envelope_names`, `test_health_answers_in_the_envelope` |
| Request bodies subclass `onb_contracts.RequestModel` (frozen, `extra="forbid"`). Never declare `org_id`/`tenant`/`org` (any spelling, any location). The box 1 test fails the build if you do. | `test_routes.py`, `test_request_inputs.py` |
| Fail on purpose with `raise AppError(ErrorCode.X, "client-safe text")`. The message goes to the client, so never interpolate user data into it. | `test_app_error_keeps_its_code_status_and_message` |
| `HTTPException` maps to the code for its status with that code's fixed message. `detail` is never shown, and only `Allow`/`Retry-After`/`WWW-Authenticate` headers are kept. Statuses under 400 pass through without the envelope. | `test_http_exception_*`, `test_wrong_method_*` |
| A validation error gives 422 `VALIDATION_ERROR`, with `details[{field, issue}]`. `field` is a declared path (dict keys become `*`, unknown fields `<extra>`), and `issue` is fixed text by error type. Submitted values never come back. | `test_validation_*`, `test_a_validator_message_*`, `test_user_chosen_keys_*`, `test_validation_details.py` |
| Error codes are stable strings (`onb_contracts.ErrorCode`), and the TS type is `ErrorCode`. Add codes, never rename. | `test_error_codes_are_stable_strings` |
| `create_app()` does not start telemetry; `onb_api.main` does (`init_telemetry("api")`). When telemetry is active, `create_app()` instruments the app. Serve with `uvicorn onb_api.main:app`. | `test_main_initialises_telemetry_as_the_api_service`, `test_requests_get_server_spans_and_meta_carries_the_trace_id` |
| After changing any API model or route: run `make contracts` and commit `libs/ts-api-client/openapi.json` and `src/generated/schema.ts`. `make check` also catches drift (`tools/tests/test_contracts.py::test_the_committed_contracts_match_the_api` runs in `tooling-test`). | `make-check.txt`, box 2 |
| TS: `createApiClient({ baseUrl?, fetch? })` defaults to `/api`, and `unwrap(await client.GET("/path"))` returns the data or throws `ApiError {code, status, message, traceId, details}`. `unwrap` is for routes that return data (null data counts as unexpected). | `libs/ts-api-client/src/client.test.ts` (6), `apps/web/src/api.test.ts` (3) |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| (none) | | | `plan.py show C-1` lists no ledger rows for this goal | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. lint, typecheck and test for 17 projects. api `50 passed`, py-contracts `85 passed` at `100.00%`, ts-api-client `6 passed`, web `4 passed`, tooling `165 passed` (`docs/evidence/C-1/make-check.txt`) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date`, exit=0 (`make-contracts-check.txt`). Box 2 has the fail/pass pair. |
| Integration | `make test-int` | exit=0. Stack `12 passed`, py-data `24 passed`, py-telemetry `3 passed`, `Successfully ran target test-int for 2 projects` (`make-test-int.txt`, ob-c-1 stack on ports 18500/18501). The API has no integration target yet. |

Cross-tenant (`make test-xt`), evals and PII-scan gates do not exist yet (T-3, EVAL-1 and SEC-1 are not done). `/health` is not tenant data.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-4 | `init_telemetry` before `FastAPI()` instruments apps built later | Partly. `from fastapi import FastAPI` at import time binds the original class, so `create_app()` calls `instrument_app(app)`. Proved by M11. |
| F-4 | `onb_telemetry.testing.captured_telemetry(name, instrument=True)` and `reset_telemetry_globals()` | yes. The default `telemetry` fixture has `instrument=False`, so the API tests use `instrument=True`. |
| F-4 | Assert scope `opentelemetry.instrumentation.fastapi` for server spans | yes (`_server_spans`) |
| F-1 | Workspace deps in the member `pyproject.toml`, then `uv lock` | yes (pydantic, fastapi, uvicorn, onb-contracts, onb-telemetry) |
| F-1 | `@onb/ts-api-client` resolves through tsconfig paths and the web vitest alias | yes |
| F-2 | `make test-int` with a per-worktree compose project | yes (ob-c-1) |

## Mutation checks
`docs/evidence/C-1/mutation-checks.txt` (script `docs/evidence/C-1/mutate.py`): `SUMMARY: 15 of 15 mutations caught`. Every file was restored byte for byte.
- M1 (box 1 seen to fail): `/health` declares an `org_id` query parameter. `test_no_request_model_or_parameter_declares_org_id` FAILED.
- M2: a `RequestModel` subclass declares `org_id`. The same test FAILED.
- M3: `is_tenant_key` always returns False. 21 walker tests FAILED, including `test_the_route_walk_finds_org_id_however_it_is_declared`.
- M4: the unhandled-error middleware is not installed. `test_unhandled_exception_returns_the_envelope_…[/test/boom]`, `[…-sync]` and the log test FAILED.
- M5: the 500 carries `repr(exc)`. Both box 4 params FAILED.
- M6: the log gets `exc_info`. `test_unhandled_exception_log_names_the_type_but_not_the_message` FAILED.
- M7: `issue` uses Pydantic's `msg`. `test_a_validator_message_carrying_the_value_is_not_echoed` and `test_issue_texts_are_fixed_per_error_type` FAILED.
- M8: `HTTPException.detail` is shown. `test_http_exception_uses_the_code_for_its_status_and_not_its_detail` FAILED.
- M9: a failure envelope is allowed without an error. `test_failure_without_an_error_is_rejected` FAILED.
- M10: the drift check compares nothing. 4 `test_contracts.py` tests FAILED.
- M11: `create_app` never instruments. `test_requests_get_server_spans_and_meta_carries_the_trace_id` FAILED.
- M12: the span gets no `error.type`. `test_an_unhandled_error_marks_the_server_span_as_failed_without_the_message` FAILED.
- M13: a dict key is kept in `field`. `test_user_chosen_keys_never_appear_in_field_locations` FAILED.
- M14: a late failure is re-raised as is. `test_a_failure_after_the_response_started_is_re_raised_without_its_message` FAILED.
- M15: the model walk checks wire names only. `test_an_alias_hiding_the_python_name_org_id_is_found` FAILED.

RED before GREEN is in `docs/evidence/C-1/red-*.txt`: contracts, API, TS client, web and contracts-check each failed on a missing import or module before its code existed.

## Review findings left open
I ran a code review and a security review (general-purpose agents with reviewer briefs, on `git diff main...HEAD`). Neither found a critical or high issue. These mediums and lows were fixed in 108e46d:
- Validation `msg` and `loc` echoed values and keys.
- A late re-raise carried the message to OTel and the logs.
- Tenant spellings like `org`, `tenant`, `org_uuid` and `X-Tenant` were missed.
- An alias could hide `org_id`, and dataclass/TypedDict bodies (including on hidden routes) were missed.
- `HTTPException(304)` became a 500.
- TS `unwrap` threw `TypeError` on a partial envelope.
- Generator stderr was hidden.

Still open:
- medium · FastAPI/OTel default span attributes · `http.url` keeps the query string, so a future route taking PII in the query would put it in traces. This is **OBS-1**'s scrubbing processor. F-4 already listed it, and no route takes query PII today.
- medium · `test_routes.py` walk · It cannot see inputs read by hand (`request.json()`, `request.query_params/headers/cookies`) or keys inside open `dict`/`Any` bodies. The test is only as strong as the declared signature. Suggested for **AUTH-1**: a middleware that rejects any request whose query, headers or cookies carry a tenant key, plus a lint or grep ban on manual `request.*` reads in routes.
- low · `onb_api/validation.py` · For a body type the resolver cannot follow (none today), every segment becomes `*`, so no PII is shown but some precision is lost. Discriminated-union branches resolve through the first non-null `anyOf` branch only.
- low · `AppError(message)` is trusted as client-safe, so a future f-string with user data would leak. This needs a review rule. A lint is possible later (SEC-1).
- low · OpenAPI walker · `$ref` parameters and `requestBody`, `not`/`if`/`patternProperties` are not resolved. FastAPI emits none of them. They only matter with `openapi_extra`.
- low · `package.json` `overrides` forces TS 6 onto openapi-typescript (peer `^5.x`). The reason and exit condition are in the `scripts/contracts.py` docstring. The byte-for-byte check would show any change in its output.
- low · `unwrap` treats `success` with `data: null` as unexpected. Add a void variant when the first no-content route appears.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. I worked to the text of `plan.py goal C-1` (the card's boxes and gates).
- No Caddy route to the API and no API service in compose. That is deployment or a later goal; the card's scope does not include it. `createApiClient` defaults to `/api` (`API_PREFIX`), so the proxy should forward `/api/*` with the prefix stripped, because the routes are unprefixed per PRD §10.

## Found for other goals
- **AUTH-1:** use `AppError(ErrorCode.UNAUTHENTICATED)` and `AppError(ErrorCode.FORBIDDEN)`. A `WWW-Authenticate` header on an `HTTPException(401)` is kept. Put the tenant-key request middleware here (see open findings).
- **UI-1:** import from `@onb/ts-api-client`, use `fetchHealth` in `apps/web/src/api.ts` as the pattern, and set a Vite dev proxy for `/api`.
- **MS-1 / REG-1 / MCP-1:** run `make contracts` after model changes. Error codes are additive only.
- **OBS-1:** scrub `http.url` and `url.query` on API server spans. The API already sets `error.type` and an ERROR status with only the exception type.
- **Deployment:** serve with `uvicorn onb_api.main:app`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`, and a Caddy route for `/api/*`.

## Decisions forced (ADRs written)
None needed an ADR. Recorded in module docstrings:
- A pure-ASGI `UnhandledErrorMiddleware` instead of an `Exception` handler, because Starlette's ServerErrorMiddleware re-raises after answering and logs the traceback.
- Named envelope subclasses for readable OpenAPI and TS names.
- `json_schema_serialization_defaults_required`, so TS types `meta`, `data`, `error` and `details` as always present.
- openapi-typescript + openapi-fetch for the client.
- The drift check is a byte comparison of both generated files, rendered in a temp directory.

## Traps hit
- FastAPI 0.162 nests included routers (`_IncludedRouter`), so iterate `fastapi.routing.iter_route_contexts(app.routes)`.
- FastAPI instrumentation's class swap misses modules that imported `FastAPI` earlier.
- The `rtk` shell hook truncates `make` output. Capture evidence with `rtk proxy make …`.
- npm `ERESOLVE` on the openapi-typescript TS peer. Solved with `overrides`.
