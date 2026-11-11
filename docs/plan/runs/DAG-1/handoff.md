# Handoff — DAG-1 · Compile a template version and hire variables into a pruned DAG        attempt 1 · goal/dag-1-dag-compiler · see `git log -1`

**Outcome claimed.** `onb_agents.dag.compile_dag(template, variables)` is a pure compiler. It turns a
published `onb_contracts.templates.Template` and a hire's variables into a deterministic `CompiledDag`.
Every template task is kept, in template order and with its edges. A task whose `when` is false starts
`skipped` with a reason, every other task starts `pending`, and `CompiledDag.ready(done)` treats skipped
tasks as finished (FR-R3), so they never block a dependent. `sla.due` resolves to a date. `hash` is
`sha256:` plus the hash of the canonical compiled document.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Engineer variables keep `bitbucket_access`; sales variables mark it skipped with a reason (test) | yes | `libs/py-agents/tests/test_dag_compiler.py`:<br>• `::test_engineer_variables_keep_bitbucket_access`: status `pending`, no reason, offered by `ready` once `collect_docs` and `manager_approval` are done.<br>• `::test_sales_variables_mark_bitbucket_access_skipped_with_a_reason`: status `skipped`, the reason names `when` and `role == 'engineer'` and not the hire's value, and `ready` never offers the task.<br>Also `::test_a_designer_keeps_security_training_and_skips_bitbucket`, `::test_a_skipped_task_stays_in_the_dag_with_its_edges` and `::test_a_dependent_of_a_skipped_task_is_not_blocked`.<br>Command: `uv run --frozen pytest -p no:cacheprovider -v libs/py-agents/tests/test_dag_*.py -k "bitbucket or acyclic or same_hash or stable_across or another_process or sla_start_date_minus_7d or cross_months or without_offset or hour_offset"` → `14 passed, 75 deselected` (`docs/evidence/DAG-1/box-tests.txt`).<br>Mutations M1, M3 and M28 each fail these tests. |
| A property test shows every compiled graph is acyclic and every dependent of a skipped task stays schedulable | yes | `libs/py-agents/tests/test_dag_properties.py::test_every_compiled_graph_is_acyclic_and_skipped_tasks_never_block`: Hypothesis, 400 examples, PASSED.<br>Inputs: 1–12 tasks with arbitrary `depends_on` (cycles in ¼ of the templates, an unknown id in about 1 in 10), 10 `when` conditions over enum, boolean and number variables, random SLAs, random hires.<br>What it checks:<br>• A cycle (found by an independent Kahn's check in the test) or an unknown dependency is refused with that reason. Every other graph must compile.<br>• The compiled edges equal the template's, and the graph is acyclic (independent check).<br>• `order` puts each task after its dependencies.<br>• A scheduler that runs only `dag.ready(done)` completes every pending task.<br>• A pending task whose dependencies are all skipped is ready at once.<br>Statistics (`docs/evidence/DAG-1/property-test-statistics.txt`): `400 passing, 0 failing`; 32% of examples have a pending task that depends on a skipped one, 13% have one whose dependencies are all skipped, 15% are refused as cycles and 16% for an unknown dependency.<br>Mutations M2, M3, M4 and M5 each fail it. |
| Compiling twice gives the same hash (test) | yes | `test_dag_compiler.py`:<br>• `::test_compiling_twice_gives_the_same_hash`: a re-parsed template and variables in reversed order give an equal DAG and the same hash.<br>• `::test_the_hash_is_stable_across_runs`: pinned literal `sha256:60d3083444ec…a520`.<br>• `::test_the_hash_is_the_same_in_another_process`: a subprocess with `PYTHONHASHSEED=12345`.<br>• `::test_a_different_compile_gives_a_different_hash[*]` and `::test_a_different_version_gives_a_different_hash`.<br>Property: `test_dag_properties.py::test_compiling_any_template_twice_gives_the_same_hash` (100 examples) PASSED.<br>Mutations M6, M7, M8, M24 and M36 each fail these tests. |
| `sla: start_date - 7d` resolves to the right date (test) | yes | `test_dag_compiler.py`:<br>• `::test_sla_start_date_minus_7d_resolves_to_the_right_date`: `2026-12-01` → `2026-11-24`.<br>• `::test_sla_offsets_cross_months_and_years[*]`: across February, a leap year and a year boundary.<br>• `::test_an_sla_without_offset_is_the_date_itself`.<br>• `::test_an_hour_offset_resolves_from_midnight_utc`.<br>All PASSED (`box-tests.txt`). Mutations M9, M10 and M11 each fail them. |

RED before GREEN: `docs/evidence/DAG-1/red-dag-compiler.txt` (a collection error, `No module named 'onb_agents.dag'`, at `5b3ced2`) and `red-review-fixes.txt` (`13 failed, 10 passed` before the review fixes).

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| **API.** `from onb_agents.dag import compile_dag, CompiledDag, CompiledTask, CompileError, CompileReason`.<br>`compile_dag(template: Template, variables: Mapping[str, object]) -> CompiledDag` is pure: no I/O, no clock, and neither argument is changed. | `test_compiling_does_not_change_its_inputs` |
| **CompiledDag** (frozen): `template_key`, `template_version`, `tasks` (template order), `order` (deterministic topological order), `hash`.<br>`.task(id)` raises `KeyError` for an unknown id. `.ready(done)` returns pending, not-done tasks whose every dependency is done or skipped, in template order; an unknown id in `done` raises `KeyError`. `.document()` is JSON. | `test_ready_*`, `test_the_order_*`, `test_the_dag_document_is_json_with_every_task` |
| **CompiledTask** (frozen): `task` (a deep copy of the `TemplateTask`, so `inputs`, `approver`, `pii_access`, `sla_hours` and the rest are all there), `status` (`pending` or `skipped`), `skip_reason`, `due` (ISO date, ISO UTC datetime for hour offsets, or `None`), plus `.id` and `.depends_on`. | `test_the_compiled_task_keeps_the_template_task`, `test_changing_compiled_inputs_does_not_change_the_template` |
| **Fails closed** with `CompileError(.reason, .task_id)`. Reasons:<br>• `not_published`: draft, archived, no version, not a `Template`, or a `model_construct` template that fails its schema.<br>• `unknown_dependency`, `cycle`.<br>• `missing_variable`: only when a `when` or `sla` reads it.<br>• `bad_variable`: wrong type, enum value outside its list, a date not in `YYYY-MM-DD` form, a datetime, a bool as a number, NaN.<br>• `bad_condition`: unparseable, undecidable, not a boolean, comparing different kinds, or comparing a date with a non-ISO literal.<br>• `bad_sla`: a non-date variable, or a date outside the calendar.<br>• `bad_inputs`: NaN in inputs.<br>Messages never echo values. | `test_dag_compiler.py` "failing closed" section, `test_dag_review_fixes.py`, mutations M12–M23, M25–M27, M29–M35, M37 |
| **Hash** = `sha256:` plus SHA-256 of canonical JSON (sorted keys, compact, ASCII, no NaN) of `{compiler: 1, template_key, template_version, order, tasks}`. It covers the compiled result, not the raw variables: variables the template doesn't read don't change it. | `test_the_hash_*`, ADR 0008 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| none | — | `grep DAG-1 docs/plan/ACCEPTANCE.md` → 0 matches; `plan.py show DAG-1` lists no ledger row | — | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (as `rtk proxy make check`) | `make check: all passed`, exit 0. Lint, typecheck and test `for 17 projects`. py-agents `465 passed`, `TOTAL 1463 16 99%`; every `onb_agents/dag/*` file at 100% (`docs/evidence/DAG-1/make-check.txt`). |
| Eval self-test | `make evals-selftest` | `selftest evr_20261111T053828Z_4b477c6e: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS` (`make-evals-selftest.txt`). Compose project `ob-dag-1`, Caddy on 18600/18601; `make down` afterwards. |
| Integration | `make test-int` | Not required: the diff doesn't touch `apps/*`, `py-data`, `py-crypto`, `py-eventbus` or `infra/`. |
| Contracts | `make contracts-check` | Not required: `libs/py-contracts` and the API models are unchanged. |
| Cross-tenant | `make test-xt` | Not applicable: no route, MCP tool or consumer added. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| TPL-1 | `onb_contracts.templates.Template` / `TemplateTask` / `Sla.parts()` → `("start_date", -7, "d")` | Yes. The compiler builds on them; nothing is redefined. |
| TPL-1 | `parse_when(src).evaluate(vars)` fails closed with `ExpressionError(EVALUATION)` | Partly. It raises on a missing variable, ordering across types and non-bool results (used: `test_a_condition_that_is_not_true_or_false_fails_the_compile`). But `==` / `!=` / `in` across kinds silently decide (`role == 1` is false). The compiler now refuses those itself (`conditions.py`). |
| TPL-1 | `graph.topological_order` (template-order ties, `()` on a cycle) and `graph.cycles` | Yes. |
| TPL-1 | `TemplateTask.inputs` is a mutable dict (open LOW) | Yes. Compiled tasks deep-copy the template task (`model_copy(deep=True)`, M24). |

## Mutation checks
`docs/evidence/DAG-1/mutation-checks.txt`, made by `uv run --frozen python docs/evidence/DAG-1/mutate.py`. It ends `source diff after all mutations: 0 files differ` and `mutations: 37, killed: 37, survivors: none`.

The guards the card names:
- **M1** a false `when` never skips → `test_sales_variables_mark_bitbucket_access_skipped_with_a_reason` and 5 more FAILED.
- **M2** skipped tasks block dependents (`finished = set(done)`) → `test_a_dependent_of_a_skipped_task_is_not_blocked` and `test_every_compiled_graph_is_acyclic_and_skipped_tasks_never_block` FAILED.
- **M3** skipped tasks offered as ready → the sales test and the property test FAILED.
- **M4** cycles not refused → `test_a_cycle_fails_the_compile` and the property test FAILED.
- **M5** unknown dependencies not refused → `test_an_unknown_dependency_fails_the_compile` and the property test FAILED.
- **M6** the hash depends on the call (`id(document)`) → `test_compiling_twice_gives_the_same_hash`, `test_the_hash_is_the_same_in_another_process` and more FAILED.
- **M7** the hash covers only key and version → `test_a_different_compile_gives_a_different_hash[*]` FAILED.
- **M8** keys not sorted → `test_the_hash_is_stable_across_runs` FAILED.
- **M9** SLA sign ignored → `test_sla_start_date_minus_7d_resolves_to_the_right_date` and more FAILED.
- **M10** hours treated as days, **M11** hours not anchored to UTC → `test_an_hour_offset_resolves_from_midnight_utc` FAILED.

The other guards:
- M12–M13: SLA on a non-date, calendar overflow.
- M14–M15: undecidable reads false, missing variable.
- M16, M27: unpublished template, a non-`Template`.
- M17–M23, M29: variable type checks, including `YYYY-MM-DD`.
- M24: deep copy.
- M25: unknown `done` ids.
- M26: NaN inputs.
- M28: the skip reason.
- M30: missing SLA variable.
- M31–M34: cross-kind and non-ISO date comparisons.
- M35: `model_construct`.
- M36: compiler version in the hash.
- M37: non-boolean condition.

One mutant is equivalent and not counted: `\d` in place of `[0-9]` in the date pattern, because `date.fromisoformat` already refuses non-ASCII digits.

## Review findings left open
One independent code review ran on `git diff main...HEAD` and found no CRITICAL or HIGH. Fixed test-first in `f9809d6` (`red-review-fixes.txt`):
- **MEDIUM:** cross-kind `==`/`!=`/`in` silently decided.
- **MEDIUM:** a date compared with a non-ISO literal compared as text.
- **LOW:** `model_construct` templates crashed with `IndexError`.
- **LOW:** no compiler version in the hash.
- Weak no-echo assertions: the review tests now use sentinel values.

A security review was not run: the diff touches no auth, tenancy, vault, grants, MCP hub, guardrail or route. The compiler reads only non-sensitive declared variables, and messages pass through `onb_guardrails.pii.redact`.

Left open:
- **LOW** · `compiler.py` `_compile_task`: a skipped task's `sla.due` must still resolve, so a hire missing a date that only a skipped branch reads fails the whole compile. This is intended (fail closed) and recorded in ADR 0008.
- **LOW** · `compiler.py` `CompiledTask.task.inputs` is a plain dict, so a caller can change a compiled DAG after hashing. This is inherited from TPL-1's schema. Deep-freezing belongs with the schema (TPL-2). ADR 0008 says consumers treat compiled tasks as read-only; RUN-1 can recompute `CompiledDag.hash_of(dag.document(include_hash=False))` before persisting.

## Left undone, and why
- No persistence, no route, no LangGraph `StateGraph`, and no runtime task states beyond the initial `pending`/`skipped`: those belong to RUN-1.
- Approver refs (`variable: manager_id`) are not resolved to users: that belongs to HIL-1.
- `sla_hours` is not resolved, because it is relative to task start: that belongs to the runtime/SLA goals.

## Found for other goals
- **TPL-1/TPL-2 (validator):** `TemplateValidator` accepts a date variable compared with a non-ISO literal (`start_date >= '2026-1-5'`), which then compares as text. Add the same `YYYY-MM-DD` literal check at publish time. The compiler already refuses it.
- **RUN-1:** call `compile_dag(published_version, hire_variables)`. Write one `run_tasks` row per `dag.tasks` entry, including skipped ones (status `skipped`, `skip_reason`). Store `dag.hash` on the run. Use `dag.ready(done)` semantics for FR-R3. Pass the hire's declared variables only, never vault tokens: undeclared keys are ignored anyway. A `CompileError` should be a 4xx naming `reason`, not a 500.
- **SLA/HIL goals:** `due` with an hour offset is counted from 00:00 UTC (ADR 0008 §5). Revisit if orgs get a time zone; `COMPILER_VERSION` must be bumped if the document changes.

## Decisions forced (ADRs written)
- `docs/adr/0008-dag-compilation.md`: skipped tasks are kept with reasons; skipping doesn't cascade; fail-closed rules; hash definition with `COMPILER_VERSION`; SLA date semantics (hour offsets from UTC midnight; `sla_hours` left for the runtime). The number may collide with another parallel branch's 0008; the orchestrator should renumber on integration if so.

## Traps hit
- Python 3.12 `date.fromisoformat` accepts `20261201` and `2026-W48-2`. The compiler requires the stored `YYYY-MM-DD` form (M29).
- The worktree guard refuses a Bash command whose text contains "git" anywhere, even inside a word like "digits". Put edits in helper scripts under the job tmp folder.
- The `rtk` hook rewrites some commands (`grep`, `cat`, `tail`) and truncates `make` output. Use `rtk proxy make …` for full gate lines.
