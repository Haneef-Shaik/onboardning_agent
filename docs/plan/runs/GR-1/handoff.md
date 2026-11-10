# Handoff — GR-1 · Guard every LLM input, output and budget        attempt 2 · goal/gr-1-guardrails-lib · see `git log -1`

**Outcome claimed.** `libs/py-guardrails` (`onb_guardrails`) now has a connector-agnostic guard,
`GuardedChatModel`. It wraps any LangChain chat model, including one with tools bound. It checks
three things:
- **Every input:** untrusted content is delimited, a heuristic detector looks for prompt injection,
  and PII is redacted.
- **Every output:** an output PII scan runs, and structured output gets one repair attempt before a
  visible failure.
- **Every budget:** steps, tokens and time per task, plus a token budget shared by the run.

Each decision is written as an audit record (through `onb_data.Auditor`, fail closed) and emitted as a
`guardrail.outcome` span event. Bedrock Guardrails are an optional, additive layer (`BedrockGuardrails`).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| An injection corpus (20+ known patterns) is detected; the benign-set false-positive rate is recorded (test) | yes | The corpus is `libs/py-guardrails/tests/injection/corpus.jsonl`: 33 positives covering **24 distinct pattern ids**, including zero-width, homoglyph, spaced-letter and full-width obfuscations, plus 30 benign onboarding messages. Three tests in `test_injection_corpus.py` pass: `test_corpus_covers_at_least_twenty_known_patterns`, `test_every_known_pattern_in_the_corpus_is_detected` (each positive must report its named pattern id) and `test_benign_false_positive_rate_is_measured_and_bounded`. **False-positive rate: 1/30 = 3.3%.** The one hit is the deliberate hard negative `neg-hard-security-training`, which quotes "ignore previous instructions" in a training context. The flagged set is pinned and capped at 10%. Command: `uv run --frozen pytest -v -s libs/py-guardrails/tests/injection/test_injection_corpus.py`. Output: `docs/evidence/GR-1/boxes.txt`. |
| Free text holding an Aadhaar and a PAN reaches the fake LLM only as markers (assert on captured input) | yes | `test_guarded.py::test_aadhaar_and_pan_in_free_text_reach_the_fake_llm_only_as_markers` PASSED (`boxes.txt`). It uses the real `fake` connector (`ScriptedChatModel`) and asserts on `model.calls()`, the captured input. That input contains `[AADHAAR]` and `[PAN]`, contains neither raw value nor the unspaced Aadhaar, and `detect()` finds nothing in any message sent. The audit record is `input.pii · AADHAAR=1,PAN=2`. Related tests cover tool results and history (`test_pii_in_a_tool_result_and_in_history_is_redacted_too`), non-text blocks, `name` and `additional_kwargs` (`test_review_fixes.py`). |
| Invalid structured output triggers exactly one repair attempt, then a visible failure (test) | yes | `test_guarded.py::test_invalid_structured_output_gets_exactly_one_repair_then_fails_visibly` PASSED. The fake is scripted with three turns: invalid, still invalid, valid. Results: `len(model.calls()) == 2`, so the third turn is never used. The repair prompt names the schema and the field. `StructuredOutputError(schema="Verdict")` is raised without the model text. The audit gets one `output.schema · failed · attempts=2; errors=1; first=json_invalid`. Companions: `test_a_repair_that_validates_is_returned_and_recorded_as_repaired` and `test_valid_structured_output_needs_no_repair`. |
| An exceeded budget stops the loop (test) | yes | `test_guarded.py::test_an_exceeded_step_budget_stops_the_agent_loop` PASSED. It runs a runaway `while True` tool-call loop with `max_steps=3`: the model is called exactly 3 times, then `BudgetExceededError("steps 3/3")` is raised and audited `budget · denied`. Also passing: the token budget (`tokens 1200/1000`, `test_an_exceeded_token_budget_stops_after_the_call_that_crossed_it`), the time budget (`seconds 31.0/30`, `test_an_exceeded_time_budget_stops_the_loop`), the shared run budget (`run tokens 1200/1000`, `test_the_run_token_budget_is_shared_by_its_tasks`) and the hard time cut-off of a hanging call (`test_parts.py::test_a_model_call_that_outlives_the_time_budget_is_cut_off`, under 2 s against a 5 s call). |
| Every decision appears as a `guardrail.outcome` span event and an audit record (test) | yes | `test_guarded.py::test_every_decision_is_a_span_event_and_an_audit_record` PASSED. It runs a structured call with a repair that fails, which makes 11 decisions across budget, input.injection, input.pii, output.pii and output.schema. The multiset of span events, keyed by (check, outcome, reason, trace id), equals the multiset of audit records keyed by (action, outcome, reason, trace_id). Every record has actor `agent:run_assistant` and target `task:run_1/task_1`, and every span carrying events has `agent.name`. Further tests: `test_events_and_audit_reasons_carry_no_raw_pii`, `test_a_failed_audit_write_fails_the_call_closed` (the model is never called), and blocked/failed paths audited for the injection and external guards (`test_external.py`, `test_review_fixes.py`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `GuardedChatModel(model=chat_model_or_bound, auditor=AuditLog(store), context=GuardContext(agent, run_id, task_id), budget=Budget(BudgetLimits(max_steps, max_tokens, max_seconds), parent=run_budget), external=None)`. `await g.ainvoke(messages) -> AIMessage` returns a redacted reply. `await g.ainvoke_structured(messages, Schema) -> Schema` allows one repair. Call either inside `tenant_scope(org)`, because the audit log needs it. | `test_guarded.py`, `test_parts.py` |
| Errors: `InjectionBlockedError(.patterns)`, `BudgetExceededError(.reason)`, `StructuredOutputError(.schema, .reason)` and `ExternalGuardBlockedError(.guard, .reason)` all subclass `GuardrailError`. Messages hold codes and counts only. A failed audit write propagates (fail closed), and so does a provider error (it still uses a step). | `test_a_failed_audit_write_fails_the_call_closed`, `test_a_failed_model_call_still_uses_a_step[*]` |
| Message trust: `SystemMessage` and earlier `AIMessage`s are trusted. **Every other role is untrusted**: it is scanned, redacted and delimited as `<untrusted_content source="chat\|tool">`. `trusted_message(text)` gives a `TrustedInstruction`, which is scanned and redacted but not delimited. Trust is the class, so a dict or checkpoint round-trip makes the message untrusted again. **Never interpolate hire, upload or tool text into `trusted_message`.** | `test_untrusted_messages_are_delimited_and_the_system_prompt_carries_the_notice`, `test_a_trust_flag_from_data_does_not_make_a_message_trusted`, `test_a_trusted_message_is_still_scanned_so_interpolated_text_cannot_inject` |
| `UNTRUSTED_NOTICE` is appended to the first system message, or a system message is added when there is none. Agents should keep a single system prompt. | `test_a_system_notice_is_added_when_there_is_no_system_message` |
| Output redaction rewrites tool-call **arguments**, so a model that puts raw PII in tool args gets `[PAN]`-style markers, never the value. Tools must take `ptk_` tokens (`ptk_` is never detected). Ids, names and other structural keys are never rewritten, so tool-call pairing holds. | `test_pii_in_the_reply_and_in_tool_call_arguments_is_redacted`, `test_tool_call_ids_and_names_are_never_rewritten`, `test_ids_survive_input_redaction_of_history` |
| Budget semantics: before a call, a used-up limit stops it; after a call, a passed limit discards the reply. Each call is one step. Tokens charged are `max(total_tokens, input+output, chars/4)`. The time limit bounds the model call and the external guard (`asyncio.timeout`). A provider's own `TimeoutError` is re-raised and is not a budget block. | `test_review_fixes.py` budget cases |
| Audit shape: `action="guardrail.<check>"` (`input.injection`, `input.pii`, `output.pii`, `output.schema`, `budget`, `external.input`, `external.output`). Outcomes map pass/redacted/repaired→success, blocked→denied, failed→failed. The span event carries `guardrail.check`, `guardrail.outcome` and `guardrail.reason`. Events link to records through the record's `trace_id`, not an id on the event (see Traps). | box 5 test |
| `BedrockGuardrails(client, guardrail_id=..., guardrail_version=...)` takes an injected `bedrock-runtime` client, and the id and version come from configuration. It sees only redacted text, plus the reply's tool-call args as JSON. It runs after the local checks, so it can only add a block, and it fails closed on error or timeout. | `test_external.py`, `test_review_fixes.py` |
| `detect_injection(text) -> tuple[pattern_id, ...]` and `PATTERN_IDS` (24) stay linear: each 200 KB adversarial input scans in ≤ 0.2 s. | `test_the_injection_scan_stays_fast_on_200kb_adversarial_input[*]` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-PII @ MS-6 | compose (fake connector), 2026-11-10 | contributes: free-text PII reaches the LLM only as markers; output PII is redacted | box 2 test, `docs/evidence/GR-1/boxes.txt` | no (MS-6 is the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (attempt 2, after removing the two test `type: ignore`s) | `make check: all passed`. Lint, typecheck and test ran for 17 projects. py-guardrails ran 307 tests at **100.00%** coverage (`TOTAL 717 0 100%`). Evidence: `docs/evidence/GR-1/make-check.txt`. |
| Eval self-test | `make evals-selftest` (compose project `ob-gr-1`, `LLM_CONNECTOR=fake`) | `27 cases x 3, all cases passed: True, gates: PASS, controls caught 8/8`, `result: PASS`, `1 passed`. Evidence: `docs/evidence/GR-1/evals-selftest.txt`. |
| Integration / test-xt / contracts | not run | The diff touches no `apps/*`, `py-data`, `py-crypto`, `py-eventbus` or `infra/` code, no route or MCP tool, and no contract. `uv.lock` gains only py-guardrails' dependency edges. |
| Eval full | not run | Needs OW-1 (Bedrock). This session is limited to the fake connector. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| PII-3 | `detect`/`redact` with typed markers; `ptk_` never detected | yes. One side effect: the OBS-1 span scrubber built on it redacts digit runs inside hex ids (Traps). |
| AUD-1 | `Auditor` protocol and `InMemoryAuditLog`; `audit()` fails closed; the record takes `trace_id` from the current span; action and target grammar | yes. Targets are `task:<run>/<task>` and actions are `guardrail.<check>`, all accepted by `build_record`. |
| F-4 | `onb_telemetry.testing.telemetry` fixture; span attribute keys `agent.name`, `run_id`, `task_id` | yes |
| LLM-1 | `fake` connector `ScriptedChatModel` with `calls()` capture and scripted usage | yes (used for boxes 2–5) |

## Mutation checks
**38/38 killed**, and the tree is clean after restore. Script: `docs/evidence/GR-1/mutate.py`; output: `docs/evidence/GR-1/mutation-checks.txt`. The first-failing test for each:
- **M1–M3** (pattern removed, skeleton off, spaced letters not joined) → `test_injection_corpus.py` corpus tests
- **M4** (injection does not block) → `test_the_local_detector_still_blocks_when_bedrock_would_pass`
- **M5** (tool results trusted) → `test_an_injection_in_a_tool_result_is_blocked`
- **M6 and M7** (untrusted or system redaction off) → box 2 test and the Bedrock redaction test
- **M8 and M9** (no delimiting, tag not escaped) → the delimiting and escape tests
- **M10 and M11** (output redaction of args or content off) → output PII tests
- **M12–M14** (no repair, a second repair, silent return) → box 3 test
- **M15–M19** (step off-by-one, no check before or after the call, run budget not shared, no time bound) → box 4 tests and `test_a_model_call_that_outlives_the_time_budget_is_cut_off`
- **M20–M22** (not audited, no span event, blocked audited as success) → box 5 and external tests
- **M23 and M24** (external guard fails open, intervention ignored) → `test_external.py`
- **M25–M38**, the review fixes → the matching `test_review_fixes.py` case

The ReDoS fix is not in the mutation script, because the old patterns run for minutes. Its RED evidence: on the pre-fix source the 200 KB cases did not finish in 600 s (`red-review-fixes.txt`).

RED before GREEN: `red-injection.txt` and `red-guarded.txt` (collection errors before implementation), and `red-review-fixes.txt` (21 of 22 regression tests fail on the attempt-1 source, plus the ReDoS timeout).

## Attempt 2
Verifier attempt 1 passed every box and gate. It failed on the quality bar (MASTER-PLAN §9.3): two
unexplained `# type: ignore` comments in tests. Both are now removed:
- In `test_guarded.py`, `_all_records` returns `list[AuditEntry]`.
- `test_review_fixes.py` builds the malformed-usage reply with `model_copy(update=...)`, which skips
  validation.

`grep -rn "type: ignore\|noqa"` over the GR-1 test and source files finds nothing.
`make check: all passed`; py-guardrails `TOTAL 717 0 100%`.

## Review findings left open
The `code-reviewer` and `security-reviewer` passes (run as general-purpose agents on `git diff main...HEAD`) found 2 high and 1 high respectively, plus mediums and lows. All highs and mediums are fixed in b7fd8f0. Left open:
- **low · `budget.py`:** a shared run budget is check-then-act. Parallel tasks can each pass the before-call check and overshoot the run limit by up to one call per task. That overshoot is then caught after the call. Reserving tokens up front was judged not worth it for MVP; documented here for AG-1/ORCH.
- **low · `guarded.py`:** a provider error (not a guardrail decision) is not audited. It is re-raised and charges a step. Auditing model failures belongs with the orchestrator's task-failure path.
- **low · audit volume:** about 6 audit writes per model step (budget is checked before and after each call). This is kept because the card says every decision is audited. If the audit collection gets heavy, a later goal could record the after-call check only when it blocks.
- **low · `messages.py`:** `detect` and `redact` each scan the text, so it is scanned twice. That is fine at prompt sizes.

## Left undone, and why
- **Bedrock Guardrails at the connector level.** This is the `guardrail_id` param that already exists in llm.yaml resolution. It is not wired into `GuardedChatModel`, and it isn't needed: the adapter is additive and connector agnostic. A live Bedrock check waits for OW-1.
- **Wiring into agents.** That is AG-1 (`GuardedChatModel` around each agent's model) and the orchestrator's run budget.

## Found for other goals
- **OBS-1 / SEC-1:** the span scrubber (`onb_telemetry.span_scrub`) redacts digit runs inside hex identifiers in span attributes. A 32-hex record id such as `…7648004092…` becomes `…[MOBILE]…`, roughly once in 10 runs here. Any goal that puts ids in span attributes must not depend on their surviving, or the scrubber needs an allowlist of id-shaped keys.
- **AG-1:** keep one system prompt. Build instructions with `trusted_message` from code or templates only. Handle `GuardrailError` subclasses as visible task failures (`StructuredOutputError` → task failed; `BudgetExceededError` → task failed with the reason; `InjectionBlockedError` → `refused`).
- **AG-1 / evals:** the dataset safety cases tb-006 and ra-007 contain "Ignore all previous instructions" and "Ignore your rules", and `detect_injection` blocks both. An agent wired through `GuardedChatModel` will raise `InjectionBlockedError` on them before the model runs. The scripted outputs must expect `refused` there.
- **AUD-1 follow-up (on its board note):** guardrail reasons are codes and counts only, so there is no reason-field PII from GR-1.

## Decisions forced (ADRs written)
None. No ADR was needed, so number 0008 is still free.

## Traps hit
- **Tracer binding.** A module-level `trace.get_tracer()` proxy stays bound to the first provider it resolves. The test fixture swaps providers, so the tracer is looked up per call (`guarded._tracer()`).
- **ruff S105.** It flags an enum member named `PASS` as a password, so the member is `GuardrailOutcome.PASSED` (value `"pass"`).
- **OBS-1 scrubber.** It mangled the audit id on span events, which made the box 5 test flaky. The id was removed from the event; records link to spans by `trace_id`.
- **Hook rewrites.** The rtk hook rewrites `find`, `git` and `cat` (`rtk find` rejects `-exec`), so `rtk proxy` or Python scripts were used for the evidence runs.
