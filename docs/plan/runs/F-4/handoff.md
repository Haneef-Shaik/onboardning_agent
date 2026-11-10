# Handoff — F-4 · Trace every Python service with OpenTelemetry        attempt 1 · goal/f-4-telemetry-lib · evidence captured at d0984ed (see `git log -1` for the handoff commit)

**Outcome claimed.** `libs/py-telemetry` (`onb_telemetry`) gives a service tracing in one call,
`init_telemetry(service_name)`. Export is OTLP/HTTP, configured from the standard `OTEL_*` env, and
FastAPI, httpx and PyMongo are auto-instrumented. `inject_traceparent()` stores a run's trace context
on a record, and `continue_from(traceparent, name)` resumes it later. A pre-export hook pipeline is in
place where OBS-1's redaction plugs in: a hook can redact or drop a span, and the pipeline fails
closed. Prompt and completion content capture is off by default, and there is an in-memory telemetry
fixture for tests.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A unit test shows a span continued from a stored `traceparent` carries the original trace id | yes | `libs/py-telemetry/tests/test_propagation.py::test_span_continued_from_stored_traceparent_carries_original_trace_id` PASSED (`uv run --frozen pytest libs/py-telemetry/tests -v -k carries_original_trace_id …`; `docs/evidence/F-4/unit-tests-coverage.txt`, last block). The test stores `inject_traceparent()` on a record, leaves the span, checks that no span is current, then runs `continue_from(record["traceparent"], …)`. It asserts that the trace id is equal and that the exported span's parent is the original span. The same behaviour is live in Jaeger: `f4.resumed-after-wait` shares the trace and has `GET /ping` as its parent (`jaeger-roundtrip-curl.txt` §3). Mutation M4 (stored traceparent ignored) makes it fail. |
| A test FastAPI app's span is returned by Jaeger's API through the compose collector (`curl` output recorded) | yes | `docs/evidence/F-4/jaeger-roundtrip-curl.txt` at d0984ed. (1) `libs/py-telemetry/tests_int/test_jaeger_roundtrip.py::test_fastapi_span_is_returned_by_jaeger_through_the_collector` passed (`1 passed`). The app runs under `init_telemetry` and exports OTLP/HTTP to `otel-collector:4318`. The test reads trace `f111c199…3934` back from `jaeger:16686/api/v3/traces/<id>` and asserts the span comes from scope `opentelemetry.instrumentation.fastapi` (not FastAPI's native tracing). (2) `curl` through Caddy, over HTTPS with basic auth and the password from a `-K` file (not shown), returns the same trace as OTLP-JSON: SERVER span `GET /ping`, two `http send` spans, and the `f4.resumed-after-wait` continuation, all with one traceId. (3) Control without credentials: `status=401`. The test is also in `make test-int` (`docs/evidence/F-4/make-test-int.txt`: `3 passed`). |
| A test shows a registered processor sees every span before export | yes | `tests/test_pipeline.py::test_registered_processor_sees_every_span_before_export` PASSED: one ordered journal shared by the hook and the exporter, and for each of 5 spans the hook's `end` entry comes before the exporter's `export` entry. Also `test_spans_from_many_threads_all_pass_the_hook_first` (24 spans, 8 threads), `test_hooks_run_in_registration_order`, `test_a_failing_hook_stops_the_span_being_exported` (fail closed), and in `tests/test_redaction.py`: `test_a_redacting_hook_changes_what_is_exported` and `test_later_hooks_see_the_earlier_hooks_redaction`. All are in `unit-tests-coverage.txt`. Mutations M1, M2, M3, M12 make them fail. |
| `libs/py-telemetry` coverage ≥ 80% | yes | `nx run py-telemetry:test --skip-nx-cache` (the target `make check` runs, with `--cov-fail-under=80`): `TOTAL 355 8 98%`, `Required test coverage of 80% reached. Total coverage: 97.75%`, `77 passed` (`docs/evidence/F-4/unit-tests-coverage.txt`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Call `init_telemetry("<service>")` once at startup, **before** building `FastAPI()`. The instrumentor swaps the `fastapi.FastAPI` class, so an app built earlier needs `instrument_app(app)`. | `test_fastapi_app_created_after_init_gets_server_spans`, `test_instrument_app_covers_an_app_built_before_init` |
| Export config is the standard env: `OTEL_EXPORTER_OTLP_ENDPOINT` (in compose, `http://otel-collector:4318`), `OTEL_TRACES_EXPORTER=otlp\|console\|none`, `OTEL_TRACES_SAMPLER`, `OTEL_BSP_*`, `OTEL_SDK_DISABLED`. Only `http/protobuf` is installed; `grpc` fails fast with a `ValueError`. | `test_settings.py`, `test_otlp_exporter_is_batched_by_default`, the box 2 test |
| `record["traceparent"] = inject_traceparent()` (W3C string, or `None` when no span is current). `with continue_from(record["traceparent"], "name", attributes=…, kind=…)` joins that trace. A missing or bad value starts a new root trace and logs a warning; it never adopts the ambient span. | `test_propagation.py` |
| Span attributes: `span_attributes(org_id=…, run_id=…, task_id=…, agent_name=…, tool_name=…)` returns a dict for span start, and `set_span_attributes(span=None, …)` sets them on the current span. Keys are `org_id`, `run_id`, `task_id`, `agent.name`, `tool.name`. Blank or non-str values raise. | `test_attributes.py` |
| **OBS-1 redaction:** subclass `onb_telemetry.pipeline.RedactingSpanProcessor`, override `redact(span) -> ReadableSpan \| None`, and build the copy with `copy_span(span, attributes=…, events=…, status=…)`. Pass it to `init_telemetry(processors=[...])`, so it is in place before the first span ends, or call `register_span_processor`. Hooks run in order before export, and each sees the previous hook's output. A raise in `on_end` drops the span. A raise in `on_start` is contained and drops the span. Nothing is exported once shutdown starts. Failure logs carry only the hook class and the exception type. | `test_redaction.py`, `test_pipeline.py`, M1–M3, M12–M15 |
| Never call `handle.provider.add_span_processor(...)`. That runs after export and bypasses the hooks. | `TelemetryHandle` docstring, M3 |
| Content capture is off by default. `ONB_TELEMETRY_CAPTURE_CONTENT=true` is the single switch. `init_telemetry` forces `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` and `OPENINFERENCE_HIDE_INPUTS`/`_OUTPUTS` to match it, and `handle.capture_content` reports it. | `test_content_capture_*`, M6, M7 |
| Tests: put `from onb_telemetry.testing import telemetry` in a conftest (with `__all__`). The fixture yields `CapturedTelemetry` (`.spans()`, `.clear()`, `.handle`). `captured_telemetry(name, processors=…, instrument=…)` is the context-manager form, and `reset_telemetry_globals()` returns the process to "never initialised". | `tests/conftest.py`, the whole unit suite |
| One init per process. A second call raises. A foreign global provider makes init raise. A failed init shuts its provider down. After `shutdown()`, a production process cannot initialise again, because OTel sets the global once. | `test_init_twice_*`, `test_init_refuses_*`, `test_a_failed_init_leaves_nothing_exporting` |
| A project's host-side tests can reach any compose service through `libs/py-telemetry/tests_int/conftest.py`'s `Tunnels.open(service, port)`, the F-2 pattern generalised and labelled per stack. | `make-test-int.txt` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U7 @ MS-6 | compose, 2026-11-10 | contributes: a run's continuation stays in one trace, visible in Jaeger | `jaeger-roundtrip-curl.txt` | no (MS-6 is the last prover) |
| NF-OBS @ MS-6 | compose, 2026-11-10 | contributes: the span pipeline and auto-instrumentation exist; no tool/LLM spans yet | `unit-tests-coverage.txt`, `jaeger-roundtrip-curl.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0. lint, typecheck and test pass for 17 projects (`docs/evidence/F-4/make-check.txt`, d0984ed) |
| Integration | `make test-int` | exit=0. Stack checks `12 passed`, py-data `4 passed`, py-telemetry `3 passed`, then `Successfully ran target test-int for 2 projects` (`make-test-int.txt`, ob-f-4 stack) |

The other gates (test-xt, contracts-check, evals-selftest, test-pii, e2e) do not exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-2 | OTLP is at `otel-collector:4318` (HTTP) and traces reach Jaeger | yes. The box 2 trace went through it. |
| F-2 | Jaeger v2's query API is `/api/v3/...` | yes. `/api/v3/traces/<id>` and `/api/v3/services` |
| F-2 | Jaeger UI/API is at `https://jaeger.localhost:<CADDY_HTTPS_PORT>` behind basic auth, HTTPS only | yes. 200 with credentials, `401` without (`jaeger-roundtrip-curl.txt` §4) |
| F-2 | The tunnel pattern and `make test-int` fan-out; a project joins by adding a `test-int` target | yes. py-telemetry's target was picked up |
| F-1 | Workspace deps go in the member's `pyproject.toml`, then `uv lock` | yes |

## Mutation checks
`docs/evidence/F-4/mutation-checks.txt` at d0984ed (script: `docs/evidence/F-4/mutate.py`). Exit 0: 16 of 16
were caught, and every file was restored byte-for-byte.
- M1: the export stage runs before the hooks. `test_registered_processor_sees_every_span_before_export` and `test_spans_from_many_threads_all_pass_the_hook_first` FAILED.
- M2: fail-open (a raising hook's span still exported). `test_a_failing_hook_stops_the_span_being_exported` FAILED.
- M3: hooks added through `provider.add_span_processor`. `test_registered_processor_sees_every_span_before_export` FAILED.
- M4: `continue_from` ignores the stored traceparent. `test_span_continued_from_stored_traceparent_carries_original_trace_id` FAILED.
- M5: a bad traceparent adopts the ambient span. `test_bad_traceparent_never_adopts_the_ambient_span[None]` and `[garbage]` FAILED.
- M6: content capture on by default. `test_defaults_export_otlp_and_capture_no_content` FAILED.
- M7: the GenAI content switches are not forced. `test_content_capture_is_off_by_default_for_genai_instrumentation` FAILED.
- M8: a foreign global provider is accepted. `test_init_refuses_when_another_provider_owns_the_global` FAILED.
- M9: no auto-instrumentation (live). `test_fastapi_app_created_after_init_gets_server_spans`, `test_httpx_calls_get_client_spans` and `test_fastapi_span_is_returned_by_jaeger_through_the_collector` FAILED.
- M10: the OTLP exporter replaced by none (live). `test_otlp_exporter_is_batched_by_default` and the Jaeger round-trip FAILED.
- M11: the test reset keeps PyMongo's stale listener (live). `test_pymongo_commands_get_spans` FAILED.
- M12: export gets the original span, not the redacted copy. `test_a_redacting_hook_changes_what_is_exported` FAILED.
- M13: an `on_start` failure is not contained. `test_a_hook_failing_on_start_does_not_break_the_caller_and_the_span_is_dropped` FAILED.
- M14: the pipeline's shutdown guard removed. `test_spans_ending_after_shutdown_are_never_exported` FAILED.
- M15: the failure log carries the exception message. `test_failure_log_names_no_span_content` FAILED.
- M16: a failed init leaves its provider exporting. `test_a_failed_init_leaves_nothing_exporting` FAILED.

RED before GREEN: `docs/evidence/F-4/red-unit-tests.txt` (plugin import error before the library existed)
and `docs/evidence/F-4/red-review-fixes.txt` (`RedactingSpanProcessor` missing before the review fixes).
The first mutation run missed M5, M9 and M11. The tests were tightened (6591ae3), and M14 was tightened
at d0984ed. All are caught now.

## Review findings left open
I ran a code review and a security review (general-purpose agents with reviewer briefs). These were fixed:
- The critical/high issue: hooks could not redact, because ended spans are read-only. Now `RedactingSpanProcessor` and `copy_span`.
- A hook raising in `on_start` broke callers.
- A failed init left an exporting provider.
- Shutdown and flush were not exception-safe.
- `set_export_processor` leaked the old processor.
- The failure log leaked span content.
- Shutdown order let spans pass through closed hooks.
- An assertion could print the Mongo password.

Still open:
- medium · `setup.py` `init_telemetry` · Spans ending before a hook is registered with `register_span_processor` are exported unhooked. Left because the API already supports `init_telemetry(processors=[redactor])`. **OBS-1** should install redaction through `processors=` (or have init install it by default) so there is no gap.
- medium · default auto-instrumentation attributes · FastAPI/httpx record `http.url`/`http.target` with query strings, plus `http.user_agent` and `net.peer.ip`. Exception events carry `exception.message`. Header capture can be switched on via `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*`. PyMongo `capture_statement` is off by default. Left for **OBS-1**, which owns the scrubbing rules (PRD §7.7).
- medium · `setup.py` `_apply_content_capture` · The content switch is env-based, so a directly passed OpenInference `TraceConfig(hide_inputs=False)` overrides it. Left for **LLM-1** (build the instrumentor from `handle.capture_content`) and **OBS-1** (a backstop that strips `gen_ai.*`, `input.value` and `output.value` unless capture is on).
- low · `setup.py` `TelemetryHandle.provider` is public, so `add_span_processor` can bypass the hooks. Documented. A guard (lint/check) could come with OBS-1.
- low · `testing.py` imports `pytest` and `opentelemetry-test-utils`, which come from the root dev group, not `onb-telemetry`'s own deps. That is fine in the single uv workspace venv; declare a `testing` extra if a service ever gets its own environment.
- low · `testing.py` touches private state (`setup._active`, PyMongo's `_commandtracer_instance`). This is documented, and M11 fails if the PyMongo attribute stops working. Re-check on a contrib upgrade.
- low · `propagation.py` stores `traceparent` only. `tracestate` and baggage do not cross async hand-offs, which is enough for "one trace per run".
- low · `_apply_content_capture` silently overrides an operator-set GenAI env var. This is intended, since there is one switch.
- low · The integration tests use the Mongo root account through a 127.0.0.1-only tunnel for the session. This is the same as F-2.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. I worked to the text printed by `plan.py goal F-4`.
- No OpenInference/LangChain instrumentation. That is LLM-1 (PRD §7.7). This lib provides the content switch and the hooks.
- No PII redaction processor. That is OBS-1. This lib provides and proves the plug-in point.

## Found for other goals
- **C-1 (API):** call `init_telemetry("api")` before `FastAPI()` (or use `instrument_app`). Put `from onb_telemetry.testing import telemetry` in the API's conftest. FastAPI 0.162 also has native tracing; it stays out of the way once the instrumentor is active (verified), but assert the scope `opentelemetry.instrumentation.fastapi` if you test spans. Starlette warns that `httpx` with `TestClient` is deprecated in favour of `httpx2`.
- **OBS-1:** see the open findings: install redaction via `processors=`, plus the attribute list to scrub. `copy_span` covers attributes, events, links and status.
- **LLM-1:** build OpenInference with `TraceConfig(hide_inputs=not handle.capture_content, hide_outputs=…)`, and register nothing on `handle.provider` directly.
- **EVT-1 / orchestrator:** store `inject_traceparent()` on event, outbox and job documents at hand-off, and wrap the consumer in `continue_from(doc["traceparent"], …, kind=SpanKind.CONSUMER)`.
- **Compose app services (later goals):** set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` in each Python service. `.env.example` is unchanged here because compose has no app services yet.

## Decisions forced (ADRs written)
None needed an ADR. Recorded in module docstrings:
- OTLP over HTTP/protobuf only, so there is no grpcio dependency.
- The hook pipeline is a single SDK processor that runs before export (the SDK appends processors after export).
- The redaction contract is a transform that returns a copy, because ended spans are immutable.

## Traps hit
- FastAPI 0.16x emits its own OTel spans (scope `fastapi`), so "a server span exists" does not prove our instrumentation. The tests assert the scope.
- `PymongoInstrumentor` creates its command listener once per process (on the singleton instance) and only disables it on uninstrument. Re-init in tests needs `_forget_pymongo_tracer()`.
- An ended SDK span is read-only (mappingproxy attributes), so a redaction hook must return a copy.
- The SDK sets the W3C Level 2 random flag, so `traceparent` ends in `-03`.
- A pytest11 entry-point plugin is imported before coverage starts, which reports 52%. Re-export the fixture from a conftest instead.
- `pytest_plugins` in a non-root conftest, and two `conftest.py` files for mypy: `tests_int/` is a package (`__init__.py`) for that reason.
- The worktree guard rejects compound shell commands that mention git or `.` sourcing. Use `uv run --env-file .env` instead of sourcing `.env`.
