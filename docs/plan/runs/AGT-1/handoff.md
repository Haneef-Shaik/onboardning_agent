# Handoff — AGT-1 · Ship the DB, Templates and Provisioning agents        attempt 1 · goal/agt-1-task-agents · base main 6ce846f (see `git log -1` for the handoff commit)

**Outcome claimed.** The three task agents run their seed-template tasks inside runs through the real hub, mocks, notify sink and MongoDB (boxes 2 and 3 proven); box 1 (eval gates on the real connector) is **not met**: Bedrock (OW-1) is unavailable, and no paid model call was made.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `db-001`…`db-006` and `tp-001`…`tp-003` meet the eval gates on the real connector (report in `docs/evidence/AGT-1/`) | **no** (waiting on OW-1) | Only the harness and grading path is proven, on the scripted fake connector: `make evals-selftest` → `selftest evr_20261112T145702Z_bf9e157c: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS` (`evals-selftest.txt`); the nine cases' per-case results: `selftest-db-tp-cases.json` (all `pass_rate 1.0`). Also `libs/py-agents/tests/test_task_agent_manifests.py` shows each scripted tool call is inside the agent's manifest (it found and fixed `templates_agent` lacking `new_hires` read). **Remaining for the real run:** with OW-1 in place, run `db-001…006, tp-001…003` on the real connector (k=3), check the gates (capability pass@1 ≥ 90%, pass^k ≥ 80%; safety pass^k 100%), and save the report here. Watch: a real model must copy `task_key`/`hire_key` and derive `profile`/`office` from `variables` (ADR 0021); `tp-001` needs the hire's name from `new_hires`. |
| A provisioning task completes through the mocks in an integration run (test) | yes | `apps/mcp-hub/tests_int/test_task_agents_int.py` (`uv run --frozen pytest apps/mcp-hub/tests_int/test_task_agents_int.py`: `5 passed`): `test_a_provisioning_task_creates_the_laptop_order_through_the_mocks` (task `done`, `result.outcome completed`, mocks store `creations +1`, hub audit record `mcp.tool_call tool:mocks.provision_laptop` SUCCESS by actor `provisioning_agent`), `test_the_run_ends_completed_and_the_order_used_the_executors_stable_references` (run `completed`; replaying the call with the derived keys creates nothing new), `test_a_bitbucket_account_task_completes_through_the_mocks`, `test_a_designer_gets_a_design_laptop`. Real hub + mcp-mocks + mcp-notify + MongoDB container; only the model is scripted. |
| An integration run of the seed template completes every non-approval task without human action; the count is recorded in the handoff | yes | `test_the_seed_template_completes_every_non_approval_task_without_human_action`. Printed: `AGT-1 seed run run_…: 7 tasks, 1 approval, 6/6 non-approval tasks completed without human action`. **Count: 6 of 6** non-approval tasks (collect_docs, laptop, bitbucket_access, security_training, orientation, welcome_message) done with `output.status == ok`; the one approval task (`manager_approval`) answers `needs_human`, which RUN-3 completes today (HIL-1 will pause it). 6/7 = 86% ≥ 70% (PRD §13). `bitbucket_access` carries `requires_approval: true`, which the engine does not act on until HIL-1; counted among the 6. The welcome message goes through the real notify sink with a grant (vault token). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `agent_input` gives every task agent `task_key` and `hire_key` (stable 24-hex digests, the mocks' idempotency key and hire id) and `variables` = the hire's `role, location, start_date, manager_id` and nothing else. | `apps/orchestrator/tests/test_agent_input.py` (8 tests); mutations M1–M6 |
| `templates_agent` reads `new_hires`, writes nothing; `db_agent` writes stay `run_tasks, documents_meta`. | `libs/py-agents/tests/test_task_agent_manifests.py`; M7–M9 |
| The seed template runs end to end through the real hub, mocks and notify sink. | `test_task_agents_int.py`; M10, M11 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| SM-2, EV-DB, EV-TP @ MS-6 | compose/testcontainers, 2026-11-12 | contributes evidence only (EV-DB/EV-TP need the real eval run) | this file | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | see `make-check.txt` (final line `exit=0` on the final tree) |
| Integration (this goal's) | `uv run --frozen pytest apps/mcp-hub/tests_int/test_task_agents_int.py` | `5 passed` |
| Eval self-test | `make evals-selftest` | `PASS`, controls 8/8 (`evals-selftest.txt`) |
| Mutations | `uv run --frozen python docs/evidence/AGT-1/mutate.py` | `mutations: 11, killed: 11, survivors: none`, `0 files differ` (`mutation-checks.txt`) |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-3 | `TaskExecutor.execute(TaskWork)` seam; grant only for `pii_access`, run's own `run_id` | yes (welcome_message got a grant the notify sink accepted) |
| NOTIF-1 | sink resolves the vault token with the run's grant | yes (real sink in the stack) |
| MOCK-1 | mocks idempotent on 24-hex `task_id` | yes, but ids needed ADR 0021 |
| REG-1 | db_agent writes stricter version | yes, not widened; pinned by test |
| EVAL-1 | selftest scripted trajectories | held except `templates_agent` manifest lacked `new_hires` read (fixed) |

## Mutation checks
`docs/evidence/AGT-1/mutation-checks.txt`: 11 of 11 killed. M3 (no length prefix) first survived because the NUL separator already separated parts; the test now covers a part containing NUL, and kills it.

## Review findings left open
Two independent reviews (general-purpose agents, code and security) on `git diff main...HEAD`: no CRITICAL or HIGH.
- **MEDIUM · `agent_input.py:41` · a missing hire gives `variables = {}`, not a failure.** A deleted or wrong-org hire lets a provisioning task run with no role/location. Left because failing would change RUN-3's tests, which have no hire records. → RUN-2/SEC-2: fail the task `hire_missing`.
- **MEDIUM · `config/agents/templates_agent.yaml` · `templates_agent` can read whole `new_hires` documents (plaintext `name`, free-text `notes`), and other hires of the same org.** Allowed by PRD §5.5/§7.10 (tokens only for sensitive values) but no hub-side field projection exists. → SEC-1 / MCP-3 follow-up.
- LOW · refs are unkeyed SHA-256 (not secrets; wording corrected); LOW · "laptop profile follows role" checks go through mock replay with hard-coded args.

## Left undone, and why
- Box 1 on the real connector (OW-1). No eval cases exist for `provisioning_agent`; add them with the real run.
- `/goal` could not be set in this orchestrated session; I worked to the text `plan.py goal AGT-1` prints.

## Found for other goals
- **HIL-1:** `requires_approval: true` (bitbucket_access) and `needs_human` on `manager_approval` currently complete; they should pause.
- **SEC-1:** hub-side projection for `new_hires` reads; `notes` write-time PII validation.
- **DEP-1:** nothing new.
- **Seed hires:** hire ids are `h_…`, mocks need 24-hex ids; ADR 0021's `hire_key` bridges it. MOCK-1 may relax its pattern instead.

## Decisions forced (ADRs written)
`docs/adr/0021-task-agent-input-and-references.md`.

## Traps hit
- `make bootstrap` was needed before `make check` (the contracts tests need `node_modules`).
- `.env` needed a `JAEGER_BASIC_AUTH_HASH` (bcrypt, `$` doubled) for `make up`.
- The tenant-scoped test hire must be `h_acme_001` so the stack's vault token matches the grant subject.
