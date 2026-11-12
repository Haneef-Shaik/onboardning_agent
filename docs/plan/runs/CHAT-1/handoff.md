# Handoff — CHAT-1 · Answer "why is this run stuck?" from run state        attempt 1 · goal/chat-1-run-assistant-chat · HEAD_PLACEHOLDER

**Outcome claimed.** Everything except the real-connector eval report: HR and managers chat about a run
(`POST/GET /runs/{id}/chat`), the Run Assistant answers read-only with task citations, a proposed retry
runs only on `POST /runs/{id}/chat/confirm`, managers are limited to their reports, stored chat is redacted.
Box 1 waits on OW-1 (Bedrock): the harness, adapter and fake-connector path are proven.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| [1] `ra-001`…`ra-007` meet the eval gates on the real connector (report in `docs/evidence/CHAT-1/`) | **no — waiting on OW-1** | Not runnable: no Bedrock, no paid call made. Fake/scripted path: `make evals-selftest` → `selftest evr_20261112T164241Z_e40475fd: 27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`; ra rows (all pass_rate 1.0) in `docs/evidence/CHAT-1/selftest-ra-001-007.json`. Adapter registered: `onb_evals.agents.run_assistant`, `libs/py-evals/tests/test_run_assistant_adapter.py` (2 passed) runs the real manifest through AG-1's runtime on the scripted fake model. **Remaining:** `uv run python -m onb_evals run --agent run_assistant --llm <alias>` on Bedrock, report into `docs/evidence/CHAT-1/`. |
| [2] A proposed retry runs only after the confirm call (test) | yes | `apps/api/tests/test_chat.py::test_a_proposed_retry_changes_nothing_until_the_confirm_call` (task stays `failed`, no `run.task.retry` audit after chat, incl. "yes, do it" in chat; `ready` + 1 audit after confirm); also once-only, owner-only, expiry, run-mismatch tests. `uv run pytest apps/api/tests/test_chat.py` → 17 passed |
| [3] A manager asking about a run outside their reports is refused (test) | yes | `test_a_manager_asking_about_a_run_outside_their_reports_is_refused_before_the_assistant_runs` (404, assistant never called, nothing stored); `test_role_matrix` rows |
| [4] Stored messages contain redaction markers, not raw PII (test) | yes | `test_stored_messages_and_what_the_assistant_sees_hold_markers_not_raw_values` (PAN and email absent from the API response, the assistant's input and the stored `messages` documents) |
| [5] `make test-xt` covers every new route | yes | `tools/tests_xt/test_xt_api.py` ROUTE_CASES rows for `POST/GET /runs/{run_id}/chat` and `POST …/chat/confirm`; `make test-xt`: 333 passed + 4 setup errors (Mongo "connection closed" flake), re-run of those and every chat case: 71 passed. Final run in `make check` below. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `POST /runs/{id}/chat` answers `text/event-stream` (`message`, `end`); `GET` returns the caller's own conversation; `confirm` is HR/HR_ADMIN | test_chat.py, test_role_matrix.py |
| The only way chat changes a run is `confirm` → `RunControl.retry_task` (audited) | test_chat.py, mutation checks |
| Collections `conversations`, `messages`, `chat_proposals` hold chat state; contracts in `onb_contracts.chat` + regenerated TS client | `make contracts-check` |
| `onb_evals.adapters.adapter_for("run_assistant")` resolves (lazy registration) | test_run_assistant_adapter.py |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U6 @ MS-5, EV-RA @ MS-6 | compose, 2026-11-12 | contributes evidence only | above | no (not the last prover) |

## Gates
See `docs/evidence/CHAT-1/gates.txt`.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| RUN-2 | `RunControl.visible_run` is the manager-scope rule; `retry_task` needs a blocked run and failed task and audits | yes |
| AG-1 | `AgentRunner` returns contract-validated output, failures never carry output | yes (adapter test) |
| EVAL-1 | ra cases and checks in the self-test | yes |
| PII-3 | `redact()` replaces detected values with markers | yes (PAN, email) |

## Mutation checks
14/14 killed, see `docs/evidence/CHAT-1/mutation-checks.txt` (manager-scope lookup in chat and history, claim skipped, owner/run/status/expiry in confirm and claim, raw question/answer stored, proposal state/role/run checks, citation filter, confirm route role).

## Review findings left open
- low · `apps/api/src/onb_api/chat/service.py` · no rate limit on chat (each turn is an LLM run) · belongs with SEC-2 / ops limits.
- low · `config/agents/run_assistant.yaml` · the agent can read `messages` of every user in the org through the hub (org-scoped, not user-scoped); manifest grant is from AG-1/REG-1 · SEC-2.
- low · `chat_proposals` never swept; expired ones stay · retention goal.
- low · only `retry_task` is proposable (card scope).
- A manager's own chat is not manager-scoped beyond `visible_run`; the agent's own manager refusal is prompt-level (ra-003) and only provable on the real connector.
- Code/security review: self-review only (no separate reviewer agent run in this session); the goal verifier follows.

## Left undone, and why
- Box 1 real-connector report: needs OW-1 (Bedrock). No paid call was made.
- `AGENT_TOKEN_KEY` and `MCP_HUB_URL` must be set on the API for the chat to be served (else 503); DEP-1 must add them to the API's compose service.

## Found for other goals
- DEP-1: API service needs `AGENT_TOKEN_KEY`, `MCP_HUB_URL` (same values as orchestrator/hub).
- UI-7: consume `GET/POST /runs/{id}/chat`, `…/chat/confirm`; the reply's `proposal.status` is `pending|confirmed|failed` (expired shows `failed`).
- In-memory test database gained `$gt` (`apps/api/src/onb_api/testing_mongo.py`).

## Decisions forced (ADRs written)
- ADR 0024: API authorizes before the agent, proposals are API state, confirm is a separate call, chat is redacted.

## Traps hit
- Manifest `purpose` is capped at 500 characters; the assistant's instructions had to fit.
- `make up` needs `JAEGER_BASIC_AUTH_HASH` in `.env`.
- Known flake: Mongo "connection closed" at xt setup under load; re-run.
