# Handoff — LLM-1 · Plug LLM providers in through a connector registry        attempt 1 · goal/llm-1-llm-connectors · evidence captured at f3675d6 (see `git log -1` for the handoff commit)

**Outcome claimed.** `libs/py-llm` (`onb_llm`) resolves a LangChain `BaseChatModel` for any agent from
`config/llm.yaml` plus env. Resolution runs agent manifest → org override → global default. Connectors
are discovered through the `onboarding.llm_connectors` entry-point group, and the built-ins `bedrock`,
`langchain` and `fake` are registered the same way. `LLMRuntime.start` fails fast when a selected
connector's env is missing. Every call gets an `llm.chat` span carrying `llm.connector`, `llm.model`
and token usage. `make llm-health CONNECTOR=…` calls each alias once. No credentials or model ids are
in code.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Resolution order has three tests (manifest wins, org override, global default) | yes | **Tests:** `libs/py-llm/tests/test_resolution.py::test_manifest_wins_over_org_override_and_default`, `::test_org_override_applies_when_the_manifest_names_no_model`, `::test_global_default_applies_when_neither_manifest_nor_org_names_a_model`. All PASSED in `nx run py-llm:test --skip-nx-cache -- -v` (`docs/evidence/LLM-1/unit-tests-coverage.txt`: `123 passed`). **Mutations:** M1 (org before manifest), M2 (org ignored) and M3 (default ignored) each fail the matching test (`mutation-checks.txt`). |
| A test-only connector package registered by entry point is discovered with no core change (test) | yes | **Test:** `test_registry.py::test_a_connector_package_registered_by_entry_point_is_discovered_with_no_core_change` PASSED. **The package:** `libs/py-llm/tests/connector_pkg/` holds `onb_llm_echo_connector` (outside `onb_llm`) and a `.dist-info/entry_points.txt` registering `echo`. **What the test does:** asserts `echo` is absent, puts the folder on `sys.path` (what installing does), then shows `discover_connectors()` finds it, and a manifest `{connector: echo, model: echo-1}` resolves and answers `"echo from echo-1"`. No file in `onb_llm` names it. **Mutation:** M4 (discovery hard-codes the built-ins) fails it. |
| Selecting `bedrock` without `AWS_REGION` fails at startup with a clear message (test) | yes | **Tests:** `test_runtime.py::test_selecting_bedrock_without_aws_region_fails_at_startup_with_a_clear_message` and `test_health.py::test_bedrock_without_aws_region_fails_at_startup_from_the_command_line` (a subprocess: exit 2, no traceback) both PASSED. **Message:** `LLM connector 'bedrock' is selected (by aliases.fast, default via aliases.standard, aliases.deep) but AWS_REGION is not set. Set AWS_REGION in the environment or .env (see .env.example), or select another connector.` The same message comes from `make llm-health CONNECTOR=bedrock` (`docs/evidence/LLM-1/llm-health.txt`, `exit=2`). **Mutations:** M6, M7, M8 and M15 fail these tests. |
| The `fake` connector replays a scripted tool-calling conversation (test) | yes | **Test:** `test_fake_connector.py::test_the_fake_connector_replays_a_scripted_tool_calling_conversation` PASSED. Through an agent loop with `bind_tools([add_days])`: (1) the model returns tool call `call_0_0` with the scripted args and usage; (2) the tool runs; (3) the model sees the `ToolMessage` and gives the scripted final answer. **Also:** `test_the_replay_is_deterministic`, `test_a_script_that_runs_out_fails_loudly` and `test_a_scripted_call_to_a_tool_that_was_not_bound_fails`. **Mutations:** M10 and M11 fail them. |
| `git grep` finds no model ids outside `config/`, `.env.example` and tests (command recorded) | yes | **Scan:** `docs/evidence/LLM-1/model-id-scan.txt` (script `model-id-scan.sh`) records `git grep -nIiE "$PATTERN" -- . ':!config/' ':!.env.example' ':!**/tests/**' ':!**/tests_int/**'`, which gives `exit=1 (1 = no match)`. **Control:** the same pattern over the whole tree hits only the guard's own test file. **Kept enforced:** `test_no_model_ids_in_code.py` runs the same scan in `make check`. **Mutation:** M14 (a model id written into `bedrock.py`) fails it. The guard first missed M14: `git grep -E` on macOS has no `\b`. The pattern is now POSIX ERE, and the guard tests `git grep` itself against sample ids (ced90ce). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Startup:** `runtime = LLMRuntime.start(load_llm_config(Path("config/llm.yaml"), os.environ), discover_connectors(), os.environ)`. It raises `LLMConfigError` subclasses (`ConnectorNotFoundError`, `ConnectorEnvironmentError`) for bad config, a missing connector or missing env. | `test_runtime.py`, `test_config.py` |
| **Per task:** `resolved, chat_model = runtime.chat_model_for(manifest=LLMSelection(...) or None, org_override=LLMSelection(...) or None)`. Store `resolved.task_record()` on the task (connector, model, alias, source; no params). `chat_model` is a new `BaseChatModel` each call. | `test_resolution.py`, `test_runtime.py` |
| `LLMSelection` has the manifest's `llm:` shape: `{alias: standard}` or `{connector, model}`, plus optional `params`. **A manifest or org override may set only `temperature`, `max_tokens`, `top_p`, `stop`.** Anything else raises `LLMConfigError`. Guardrail, region, endpoint and fake-script settings come only from `llm.yaml` `connectors.<name>.params`. | `test_a_manifest_or_org_override_may_only_set_sampling_params`, M16 |
| `config/llm.yaml` names aliases `fast`, `standard` and `deep`, and `default` is `standard`. **Models:** each alias's model comes from `LLM_MODEL_FAST`/`_STANDARD`/`_DEEP`, which are required even for `fake` (any value works). **Connector:** `LLM_CONNECTOR` (default `bedrock`) switches every alias at once. **Bedrock:** gets `region_name` from `AWS_REGION` and an optional guardrail from `BEDROCK_GUARDRAIL_ID`/`_VERSION`. | `test_the_repository_config_*` |
| **Fake model.** `fake` returns a `ScriptedChatModel`. Each call replays the next scripted turn; extra calls raise `ScriptExhaustedError`, and tool calls must name a bound tool. **Usage:** taken from the script, else one token per word. **Inspection:** `chat_model.calls()` gives every call's messages and bound tool names (for assertions on captured LLM input, e.g. GR-1). **Supplying a script:** pass `params={"script": [...]}` directly to `FakeConnector().create_chat_model`, or set `LLM_FAKE_SCRIPT_FILE` (JSON) so every fake model replays that file. | `test_fake_connector.py`, `test_the_repository_config_passes_the_region_and_a_fake_script_through_params` |
| **Spans:** one `llm.chat` CLIENT span per call (sync, async and streaming), nested under the current span. **Attributes:** `llm.connector`, `llm.model`, `llm.usage.input_tokens`/`output_tokens`/`total_tokens`. **On failure:** `error.type` and status ERROR, with the exception type only. **Never:** prompt or completion text. **Callbacks:** the connector's own callbacks (list or manager) are kept. | `test_runtime.py` span tests, M12, M13, M19; async and stream checked by the code reviewer |
| **New connector:** a package that registers `<name> = module:Factory` under `[project.entry-points."onboarding.llm_connectors"]`. `Factory()` must return an object with `name`, `required_env`, `create_chat_model(model, params)` and `healthcheck(model, params)`. The entry-point name must equal `name`. | `test_registry.py` |
| `make llm-health CONNECTOR=<name>` sources `.env`, sets `LLM_CONNECTOR`, and calls every alias once. Exit 0 means all healthy, 1 means some failed, 2 means a config or env error. CONNECTOR must match `^[a-z0-9_-]+$`. | `docs/evidence/LLM-1/llm-health.txt`, `test_health.py` |
| **Errors never print values:** config errors give key and message only; provider build errors give exception type and param names; health failures give exception type. | `test_a_validation_error_never_shows_an_interpolated_value`, `test_*_reports_a_build_failure_*`, M17, M18 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U10 @ MS-6 | compose host, 2026-11-10 | contributes: an agent's model and connector are switched by config (manifest `llm:`, org override, `LLM_CONNECTOR`, `LLM_MODEL_*`) with no code change | `unit-tests-coverage.txt` (resolution and registry tests) | no (MS-6 is the last prover) |
| NF-PORT @ MS-6 | compose host, 2026-11-10 | contributes: a new connector is a new package (box 2), and a model swap is env only | `unit-tests-coverage.txt`, `model-id-scan.txt` | no (MS-6 is the last prover) |
| SM-4 @ MS-6 | compose host, 2026-11-10 | contributes: the connector layer exists; no real model call yet (OW-1) | as above | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, `exit=0`. Lint, typecheck and test ran for 17 projects. py-llm coverage `TOTAL 584 7 99%` (`docs/evidence/LLM-1/make-check.txt`, `unit-tests-coverage.txt`, at f3675d6) |

Not run: `make test-int`. The diff does not touch `apps/*`, `py-data`, `py-crypto`, `py-eventbus` or `infra/`, and py-llm has no integration target. No real model was called. The other gates (test-xt, contracts-check, evals-selftest, test-pii, e2e) don't apply to this diff, and some don't exist yet.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-4 | `from onb_telemetry.testing import telemetry` in a conftest gives an in-memory span fixture that resets the globals | yes, but a tracer taken once at import binds to a stale provider across resets. The span handler now calls `trace.get_tracer` per span. |
| F-1 | Workspace deps go in the member's `pyproject.toml`, then `uv lock`; Nx targets lint, typecheck and test with ≥ 80% coverage | yes |

## Mutation checks
`docs/evidence/LLM-1/mutation-checks.txt` (script `docs/evidence/LLM-1/mutate.py`, exit 0): 19 of 19 caught, every file restored byte-for-byte. M1–M3 resolution order; M4–M5 discovery and name check; M6–M8, M15 fail-fast env; M9 strict `${VAR}`; M10–M11 fake replay guards; M12–M13, M19 span handler; M14 model-id guard; M16 override-param allowlist; M17–M18 no values in errors. The failing test names are listed under each mutation. RED before GREEN: `red-unit-tests.txt` (import errors before the library existed) and `red-review-fixes.txt` (18 failures before the review fixes).

## Review findings left open
- **MEDIUM (design, for AG-1/EVAL-1)** · `connectors/fake.py`: with `LLM_CONNECTOR=fake`, every alias replays the same `LLM_FAKE_SCRIPT_FILE` (or "OK"). A harness that needs a different script per case should build the model with `FakeConnector().create_chat_model(model, {"script": …})`, or register its own test connector. There is no per-call script seam in `LLMRuntime`, because the PRD names none.
- **LOW** · `config/llm.yaml`: `LLM_MODEL_*` are required even for `fake`. Kept strict so that a missing real model id fails loudly. Documented in `.env.example`.
- **LOW** · `connectors/script.py`: `script_file` may name any path. It is set only through `llm.yaml`/env, since overrides can't set it after the allowlist fix.
- **LOW** · registry load errors include the plugin's exception message. These are local import errors from installed code, not provider responses.
- **Not verified (OW-1):** whether `ChatBedrockConverse` streaming fills `usage_metadata`. If it doesn't, streamed Bedrock spans have no token attributes.

## Left undone, and why
- No real Bedrock call: that is OW-1 (credentials, three model ids, `make llm-health CONNECTOR=bedrock` green).
- Where org overrides are stored (Mongo org settings) and their API/UI: `LLMRuntime.resolve` takes the override as an argument. Storing it belongs to the agent registry and admin goals (FR-A1/FR-A2).
- OpenInference LangChain instrumentation (PRD §7.7) belongs to OBS-1/F-4 follow-ups. The `llm.chat` span covers the PRD §7.1 attributes until then.

## Found for other goals
- **AG-1:** record `resolved.task_record()` on the task when it starts. PRD §7.1 says running tasks keep their model.
- **EVAL-1 / evals-selftest:** use `LLM_CONNECTOR=fake` with `LLM_MODEL_*=<any>`, and per-case scripts through `FakeConnector().create_chat_model(..., {"script": ...})`.
- **GR-1:** `ScriptedChatModel.calls()` exposes exactly what the LLM was sent, for the "only markers reach the fake LLM" box.
- **OW-1:** check streamed token usage on Bedrock; fill `LLM_MODEL_*` and `AWS_REGION` in `.env` only.

## Decisions forced (ADRs written)
None written; these are small and recorded here and in the code:
- **Protocol signatures:** `healthcheck(model, params)` takes the alias's model. The PRD writes `healthcheck()`, but OW-1 must check three aliases. The protocol also gains `required_env` for the fail-fast check. This is an extension, not a contradiction.
- **Override params (security review HIGH):** manifest and org-override params are limited to sampling params. HR_ADMIN (a tenant role) sets overrides (U10), and free-form params could switch Bedrock Guardrails off (§8.1: guardrails are additive) or redirect the endpoint. Switching connector or model is still allowed, as U10 requires.

## Traps hit
- macOS `git grep -E` has no `\b`, so the first model-id guard matched nothing and passed vacuously. Mutation M14 caught it.
- `ChatBedrockConverse` takes the guardrail as `guardrails=` (alias of `guardrail_config`), and it reports `region_name=None` unless the region is passed in.
- `pydantic.model_copy` shares private attributes, so a copied fake model shared its call log with the original. `ScriptedChatModel.__copy__` now starts fresh.
- The rtk shell hook truncates captured command output, so evidence files were captured through `rtk proxy`.
