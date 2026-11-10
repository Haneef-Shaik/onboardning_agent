# Checkpoint — C-1 · Stand up the API with one envelope and a generated TS client

Branch `goal/c-1-api-contracts` (from `main` @ 185b674). Status: built, reviewed, gates green; handoff written; next is the goal-verifier.

## Steps done
1. `py-contracts`: envelope (`Envelope`, `ErrorEnvelope`, `ok`, `fail`), `ErrorCode` + `ERROR_STATUS`, `PageMeta`/`Meta`, `RequestModel` (extra=forbid), tenant-input walkers (`is_tenant_key`, `tenant_inputs_in_model/type/openapi`), `Health`/`HealthEnvelope`.
2. `apps/api`: `create_app()`, `/health`, error handling (`AppError`, HTTPException, validation, `UnhandledErrorMiddleware`), `respond()` with `meta.trace_id`, `main.py` (telemetry first), `openapi.py` export.
3. Route walk test for `org_id` (box 1), seen to fail (mutation M1).
4. `scripts/contracts.py` + `make contracts` / `make contracts-check`; committed `libs/ts-api-client/openapi.json` and `src/generated/schema.ts`.
5. `ts-api-client`: `createApiClient`, `unwrap`, `ApiError`; `apps/web/src/api.ts` imports it (box 3).
6. Evidence: box 2 drift script, mutation script (15/15), gates.
7. Code + security review; mediums fixed in 108e46d (validation details, late re-raise, tenant keys, walker gaps, TS unwrap, generator stderr).

## Next
Goal-verifier pass; then board `in review` (orchestrator holds the merge).

## Surprises
- FastAPI 0.162 keeps included routers nested (`_IncludedRouter`), so `app.routes` has no `APIRoute`s for them; walk with `fastapi.routing.iter_route_contexts`.
- `from fastapi import FastAPI` binds the class before `init_telemetry` can swap it, so `create_app()` calls `instrument_app(app)` when telemetry is active.
- Pydantic generic names (`Envelope_Health_`) leak into OpenAPI; named subclasses (`HealthEnvelope(Envelope[Health])`) fix it.
- openapi-typescript 7.13 peers TS `^5.x`; `package.json` `overrides` pins it to the repo's TS 6.
- The `rtk` hook truncates `make` output; evidence was captured with `rtk proxy make …`.
