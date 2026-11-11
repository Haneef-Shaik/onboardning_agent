# Handoff — TPL-1 · Validate templates into safe task graphs        attempt 1 · goal/tpl-1-template-validator · see `git log -1` (evidence captured at 7172c52)

**Outcome claimed.** `onb_contracts.templates.Template` is the template schema. `onb_agents.templates.TemplateValidator` enforces every FR-T3 rule against:
- the registry's active agents;
- the tool catalog;
- the field → sink policy.

Each problem comes back as a named `Rule`. `parse_when` parses `when` conditions with a closed recursive-descent grammar (no `eval`, `exec`, `compile` or `ast`). `templates/engineer-india.yaml` validates, and so does every template in `evals/fixtures/seed_v1.json`.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| One named failing test per FR-T3 rule: cycle, unknown agent, tool outside the allowlist, undeclared variable, approval without approver, sensitive field in LLM-visible inputs, `pii_access` not allowed by the sink policy | yes | All tests are in `libs/py-agents/tests/test_template_validator.py`:<br>• `test_a_cycle_is_rejected[*]` (Rule.CYCLE)<br>• `test_an_unknown_agent_is_rejected[*]` and `test_a_retired_agent_counts_as_unknown` (UNKNOWN_AGENT)<br>• `test_a_tool_outside_the_agents_allowlist_is_rejected[*]` and `test_an_action_the_catalog_does_not_give_the_agent_is_rejected[*]` (TOOL_NOT_ALLOWED)<br>• `test_an_undeclared_variable_is_rejected[*]` (UNDECLARED_VARIABLE: `when`, approver, `sla.due`, `{{ }}`/`${}` in inputs)<br>• `test_an_approval_without_an_approver_is_rejected` (APPROVER_MISSING)<br>• `test_a_sensitive_field_in_llm_visible_inputs_is_rejected[*]` (SENSITIVE_INPUT)<br>• `test_pii_access_not_allowed_by_the_sink_policy_is_rejected[*]` (PII_ACCESS_FORBIDDEN)<br>Command: `uv run --frozen pytest libs/py-agents/tests/test_template_validator.py -q -k "<those seven names>"` → `45 passed, 113 deselected`.<br>Each one fails when its guard is broken: M1, M2, M4, M5, M7, M11, M14, M22 in `docs/evidence/TPL-1/mutation-checks.txt`. |
| The seed template and every template in `evals/fixtures/seed_v1.json` validate (test) | yes | `test_the_seed_template_validates` PASSED, `test_every_template_in_the_eval_fixture_validates[org_acme/engineer-india@v3]` PASSED, `[org_acme/general-india@v1]` PASSED (`-k "seed_template_validates or every_template_in_the_eval_fixture"` → `3 passed`). The fixture's `org_id` is dropped before validating; it is the storage envelope, not part of a template. |
| The `when` parser rejects function calls, attribute access and imports (test) | yes | `libs/py-agents/tests/test_when_expr.py::test_the_when_parser_rejects_function_calls[*]`, `::test_the_when_parser_rejects_attribute_access[*]`, `::test_the_when_parser_rejects_imports[*]` → `17 passed`. Each asserts the `ExpressionReason`. M29 (call guard off), M30 (attribute guard off) and M31 (import guard off) each fail them. `test_the_module_never_uses_eval_exec_compile_or_python_ast` checks the module's AST statically. |
| `pii_access` for Aadhaar is rejected (test) | yes | `test_pii_access_for_aadhaar_is_rejected[*]`: Aadhaar alone and with mobile, at `notify.telegram`, `notify.email`, `notify.sms` and `uidai.verify` → `8 passed`. Two layers reject it: the `NEVER_SINKABLE` floor and the policy. With both off (M23), all 8 fail (`docs/evidence/TPL-1/mutation-m23-aadhaar.txt`). |

RED before GREEN: `docs/evidence/TPL-1/red-*.txt` (each a collection error or the failing list, captured before the code existed). The files are `red-when-expr`, `red-template-contracts`, `red-template-validator`, `red-eval-contract`, `red-review-fixes` and `red-security-fixes`.

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **Schema.** `from onb_contracts.templates import Template, TemplateTask, Variable, Approver, PiiAccess, Sla`. Models are frozen, `extra="forbid"`, and errors never echo the input (`hide_input_in_errors`). Fields: `key`, `name?`, `version?`, `status` (default `draft`), `variables` (≤50), `tasks` (1–100). A task has `id, title, agent, action, type (action\|approval), depends_on, when, inputs, tools, pii_access, approver, requires_approval, sla \| sla_hours`. `inputs` are JSON with ASCII-name keys, nest at most 6 deep, hold strings of at most 2000 chars and at most 16 000 chars in total. The schema has no `org_id`. | `libs/py-contracts/tests/test_template_contracts.py` (100% of `templates.py` in the gate run) |
| **SLA.** `Sla(due="start_date - 7d").parts()` → `("start_date", -7, "d")`. The unit is `d` or `h`, and no offset means `0d`. A task has `sla` or `sla_hours` (the fixture form), never both. | `test_the_sla_due_parses_into_variable_offset_and_unit`, `test_a_task_has_one_sla_form_only` |
| **Validate.** `TemplateValidator.from_registry(registered_agents, catalog, sink_policy)` uses active agents only. `from_config(Path("config"))` reads the YAML files. `.validate(data)` never raises for a bad template. It returns `TemplateReport(template, issues, order)`: `ok`, `rules`, `issues[i].document()` = `{rule, task_id, message}`, and `order` (a deterministic topological order, `()` when cyclic). `.require_valid(data)` returns the `Template` or raises `TemplateInvalidError(.issues)`. | `test_template_validator.py` |
| **DAG-1.** `parse_when(src).evaluate(variables)` returns a bool. It raises `ExpressionError(EVALUATION)` on a missing variable, an unsupported value type, ordering across types or a non-bool result; it never returns false silently. `True == 1` is false. Also available: `.names`, `.comparisons()`, `.name_pairs()`. | `test_when_expr.py::test_accepted_conditions_evaluate[*]`, `::test_evaluation_fails_closed_on_missing_or_mistyped_values[*]` |
| **Messages** name rules, tasks, fields and positions (input keys as `inputs[1][0]`). Raw values never appear, and every message passes through `onb_guardrails.pii.redact`. | `test_a_schema_error_is_reported_without_the_input`, `test_input_findings_name_the_key_by_position`, `test_a_cycle_message_does_not_echo_raw_values`, M50–M53 |
| **Evals.** `onb_evals.contracts.DEFAULT_CONTRACTS["template"] == ("template", onb_contracts.templates.Template)`. `ChangeRequest.diff.add_tasks` are `TemplateTask`. | `libs/py-evals/tests/test_template_contract.py`, `make evals-selftest` PASS |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| none | — | `plan.py show TPL-1` lists no ledger row for this goal | — | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | `make check: all passed`, exit=0 (`docs/evidence/TPL-1/make-check.txt`). lint, typecheck and test ran for 17 projects. py-agents `TOTAL 1233 16 99%`; templates `expr` 99%, `graph`, `sensitive` and `validator` 100%; `onb_contracts/templates.py` 98%. |
| Integration | `make test-int` | `NX Successfully ran target test-int for 7 projects`, exit=0 (`make-test-int.txt`). Compose project `ob-tpl-1`, Caddy on 127.0.0.1:18400/18401. |
| Eval self-test | `make evals-selftest` | `selftest evr_20261111T051226Z_a4b4348e: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS` (`make-evals-selftest.txt`). It now uses the real template schema. |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` (`make-contracts-check.txt`). No route was added. |
| Cross-tenant | `make test-xt` | Not applicable: no route, tool or consumer. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| REG-1 | `load_manifests`, `load_catalog`, and `RegisteredAgent(name, version, sha256, active, manifest, updated_at)`. The catalog's `action → agent` is the same list as the fixture's `action_catalog`. | Yes. Used by `from_config` and `from_registry`; a retired agent is refused (`test_a_retired_agent_counts_as_unknown`). |
| REG-1 | `names_a_forbidden_collection` matches `pii_vault*`, `org_keys` and `pii_access_log`. | Yes. A vault name in an input is refused (`{"collection": "pii_vault_aadhaar"}`). |
| PII-4 | `load_sink_policy(path)`: `.allows(field, sink)` refuses `NEVER_SINKABLE`, and the loader refuses a config that lists one. | Yes. This is the second layer under the validator's own `NEVER_SINKABLE` check (M22, M23). |
| GR-1 | `onb_guardrails.pii.detect` and `redact`, and the `skeleton` fold. | Yes. They find raw values (also in numbers and identifiers) and fold Unicode spellings. `skeleton` is imported from `onb_guardrails.pii.skeleton`, because the package does not re-export it. |
| EVAL-1 | `DEFAULT_CONTRACTS[target]` can be replaced without changing the check. | Yes. Self-test PASS. |

## Mutation checks
`docs/evidence/TPL-1/mutation-checks.txt`, made by `uv run --frozen python docs/evidence/TPL-1/mutate.py`. It ends `source diff after all mutations: 0 files differ` and `mutations: 56, killed: 56, survivors: none`.

The guards named by the card:
- **M1** cycles never found → `test_a_cycle_is_rejected[edges0]` and the other cycle tests FAILED.
- **M2** unknown agent not reported → `test_an_unknown_agent_is_rejected[hr_bot]` FAILED.
- **M3** retired agents kept → `test_a_retired_agent_counts_as_unknown` FAILED.
- **M4** every tool granted → `test_a_tool_outside_the_agents_allowlist_is_rejected[bitbucket_access-tools0]` FAILED.
- **M5** catalog agent not compared → `test_an_action_the_catalog_does_not_give_the_agent_is_rejected[bitbucket_access-request_documents]` FAILED.
- **M7, M8, M9, M10** undeclared variable in `when`, approver, `sla.due`, and inputs placeholder → `test_an_undeclared_variable_is_rejected[...]` FAILED.
- **M11** approval without approver → `test_an_approval_without_an_approver_is_rejected` FAILED.
- **M14** inputs not scanned → `test_a_sensitive_field_in_llm_visible_inputs_is_rejected[inputs0]` FAILED.
- **M22** sink policy not consulted → `test_pii_access_not_allowed_by_the_sink_policy_is_rejected[pii_access0]` FAILED.
- **M23** never-sinkable floor and policy both off → `test_pii_access_for_aadhaar_is_rejected[*]` 8 FAILED (`mutation-m23-aadhaar.txt`).
- **M29, M30, M31** call, attribute and import guards off → the matching `test_the_when_parser_rejects_*` FAILED.

The rest:
- M6, M12, M13 and M15–M21: sensitive-scan layers, approver checks and approval type.
- M24–M28: sink server, unknown dependency, enum, literal and SLA types.
- M32–M38: dunder, subscript, depth, length, bool-vs-number, non-bool and missing-variable guards.
- M39–M40 and M47–M50, M56: schema guards (echo, unique ids, input caps, key pattern, lists).
- M41–M46: Unicode fold, part names, camelCase, numbers, path forms, identifier scan.
- M51–M55: message hygiene, ASCII digits, variable-vs-variable types.

## Review findings left open
Two independent reviews ran on the diff: a code review and a security review. Neither found anything CRITICAL. All HIGH and MEDIUM findings were fixed test-first in 3dcfeee (`red-review-fixes.txt`, `red-security-fixes.txt`):
- part-name and spelled-around sensitive names (`personal_email`, `hire.emailId`, `mob​ile`, Cyrillic `а`, full-width, camelCase);
- numeric PII in inputs;
- quadratic path regex (a 40 000-char string took 7–11 s);
- raw PII in ids, approver refs and variable names;
- values echoed in repeat, cycle, schema-loc and input-key messages;
- non-ASCII digits and spaces in `when`;
- variable-vs-variable type checks;
- unbounded lists.

The worst case at the limits now validates in 0.78 s: 100 tasks, each at 16 000 chars of hostile patterns.

Left open:
- **LOW** · `onb_contracts/templates.py` `TemplateTask.inputs`: a plain `dict` inside a frozen model, so a caller can mutate it after validation. The validator never mutates input (`test_the_validator_does_not_change_its_input`). Consumers (DAG-1) should copy before changing it. Deep-freezing would change how the field serializes in the API, which belongs to TPL-2.
- **LOW** · `sensitive.py`: prose that names a field without a path or placeholder ("reply by email.", "bring your PAN card") is allowed on purpose. It is not a reference and resolves nothing. Raw values in prose are still refused.
- **LOW** · `validator.py` `from_config`: reads the config manifests, which have no retired state. Only `from_registry` filters retired agents. TPL-2 should validate against the stored registry (`from_registry`).

## Left undone, and why
- No API route, no persistence and no lifecycle (draft → published → archived): those belong to TPL-2. No DAG compilation: that belongs to DAG-1.
- `TaskUpdate` in `onb_evals.contracts` (the change-request update shape) is still interim. It belongs to TB-1.

## Found for other goals
- **TPL-2:** validate with `TemplateValidator.from_registry(await registry.list_agents(), catalog, policy)`. Return `report.issues[*].document()` from `POST /templates/drafts/{id}/validate`. `version` and `status` are lifecycle-owned. Storage adds `org_id` from the token.
- **DAG-1:** use `report.order`, `Sla.parts()` and `parse_when(...).evaluate(hire_variables)`. An `ExpressionError(EVALUATION)` at compile time (for example a missing variable) should fail the compile, not skip the task.
- **HIL-1:** a `requires_approval: true` task may omit `approver`. The seed `bitbucket_access` does. HIL must define the default approver; HR is the obvious choice.
- **SEED-1:** `templates/engineer-india.yaml` carries no `version` or `status`. Seed it through TPL-2's publish path, or as v1 published.
- **TB-1:** the Template Builder's draft output is checked by `schema_valid` against the real `Template` now. A builder that outputs `org_id`, a non-ASCII input key or a list longer than 50 fails `schema_valid`.

## Decisions forced (ADRs written)
None (0008 is still free). Recorded here:
1. **PRD conflict, reported and not built:** the PRD §6.1 example's approval task has no `action`. The schema requires one (`action: approval`, the tool catalog's action for approval tasks, as in the eval fixture and the scripted outputs), so every task maps to a catalog entry.
2. Both SLA forms are accepted: PRD `sla: {due: "start_date - 7d"}` and the fixture's `sla_hours: 48`. A task has one or the other.
3. "Tool outside the allowlist" covers three checks: the task's `tools`, its `action` (the catalog must give it to that agent), and the server of a `pii_access` sink (the agent must be granted it).
4. An approver may sit on `type: approval` or `requires_approval: true` tasks only. A `variable` approver must be a `user_ref`.

## Traps hit
- The `rtk` hook truncates `make` output. Use `rtk proxy make …` to keep the full result lines.
- The worktree guard refuses compound commands that mix heredocs and git. Write helper scripts to the job tmp folder, then run them.
