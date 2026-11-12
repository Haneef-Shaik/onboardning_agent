# Handoff — SEC-1 · Prove zero PII leaks and 100% tamper rejection        attempt 1 · goal/sec-1-pii-leak-tamper-suite · HEAD: see `git log -1`

**Outcome claimed.** `make test-pii` runs a full seed-template run with synthetic fixture PII through the real
API, bus worker, agents, MCP hub, sinks and telemetry on the compose stack. It then scans every non-vault
Mongo collection (GridFS, `mock_outbox` and the events included), the application log, every container log
since the session started, the session's Jaeger traces, every LLM input and every API response. The scan
finds **0 hits**. The same target runs **231/231 vault, grant, token and document tamper cases, all
rejected**. CI runs it as the `pii` job.

**Entry gate.** HIL-1, CHAT-1 and AGT-1 are `on hold` on the board, not `done`. In each case the code is
merged to main and only the real-connector eval (OW-1) is outstanding. The orchestrator dispatched SEC-1
with those goals' findings in scope, so I started it. Compose stack `ob-sec-1`, Caddy on 19700/19701.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| `make test-pii` reports zero hits (report in `docs/evidence/SEC-1/`) | yes | `make test-pii` gives `SEC-1 PII scan: {"api_responses": 2754, "app_logs": 4122, "container_logs": 15402, "events": 181, "jaeger": 44904, "llm_inputs": 2884, "mock_outbox": 29, "mongo": 4178}, hits=0` and `4 passed`. The report is `docs/evidence/SEC-1/pii-scan-report.md` (`.json` alongside).<br>**Flow evidence (the scan saw real data):** run completed, 7/7 tasks done; 7 vault records and 1 record in the Aadhaar partition; the sink resolved with a grant 3 times; the welcome message reached `mock_outbox`; notes and chat were stored redacted; the run assistant's model got the redacted question; the invalid hire got a 422; 826 spans in 170 traces; 16 LLM calls.<br>**Test:** `tools/tests_pii/test_pii_scan.py::test_a_full_run_leaves_no_raw_pii_outside_the_vault`. |
| A planted leak is caught by the scan (mutation check, output recorded) | yes | `docs/evidence/SEC-1/mutation-checks.txt` (script `mutate.py`): `result: 14/14 as expected`; the only diff afterwards is the evidence file itself. Planted production leaks, each **KILLED** by the full-run scan:<br>• P1 `raw:mobile_updated in mock_outbox at mock_outbox[0].recipient.chat`<br>• P2 notes unredacted: `raw:… in mongo at new_hires[0].notes` and `in api_responses`<br>• P3 chat unredacted: `raw:… in mongo at messages[0].content`<br>• P4 log scrubber off: `raw:email in app_logs at app.log:72.message`<br>• P5 span scrubber off: `raw:email in jaeger at resourceSpans[143]…attributes[0]`<br>• P6 scan skips `mock_outbox`: the check "something was scanned from mock_outbox" fails.<br>Control C1 (same log leak with the scrubber on) stays clean, showing the layers stack. Unit level: `tools/tests/test_pii_scanner.py::test_a_planted_value_is_found_in_every_kind_of_source` (10 source kinds). |
| The tamper report lists every case with 100% rejected | yes | `docs/evidence/SEC-1/tamper-report.md`: **231/231 rejected (100.0%)**, cases listed in four categories (vault, grant, token, document), from `tools/tests_pii/tamper_cases.yaml` (123 node ids; parametrized cases expand to 231). A case counts as rejected only if its test passed; a skip, error, timeout or not running counts as not rejected (`test_only_a_passed_case_reads_as_rejected_in_the_junit_results`, mutant S6).<br>**Completeness guard:** `tools/tests/test_tamper_manifest.py`, in `make check`. Every tamper-named test in apps/ and libs/ must be listed or excluded with a reason (2 excluded), and every listed test must exist (mutant S5).<br>**Mutant T1** (vault signature check off) drops the report to `202/218 rejected (92.66%)` (manifest at that time). |
| CI runs `make test-pii` | yes (workflow in repo; no remote, F-3) | `.github/workflows/ci.yml` job `pii` (`needs: check`): `make bootstrap`, then `uv run --frozen python tools/ci_env.py > .env`, then `make test-pii`, then show the reports, then `make down` under `always()`. Guarded by `tools/tests/test_ci_pii.py` (4 tests, in `make check`). Per ADR 0001 there is one workflow, so `ci.yml` is F-3's file verbatim plus the `pii` job. No hosted run exists: F-3 has no remote yet, and nothing was pushed. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `make test-pii` (needs `.env` and Docker) brings the stack up, runs the scan and the tamper report, and writes `reports/test-pii/{pii-scan-report,tamper-report}.{md,json}` (git-ignored; `$PII_SCAN_REPORT_DIR` overrides). | Makefile target; `test_ci_pii.py` |
| The scan never excuses a raw fixture value. A detector hit is excused only inside a hex id with a known platform prefix, a `ptk_` token or a UUID, or by a named allow rule (2 today: platform users' login emails in `users`, and Caddy's `GOMEMLIMIT`). | `test_pii_scanner.py` (S2, S3) |
| Reports name locations and types only, never values; a value stored as a key shows up as `<key#i>`. | `test_the_report_names_locations_never_values`, `test_a_value_stored_as_a_key_never_reaches_a_location` |
| A new tamper-named test must be added to `tamper_cases.yaml` or `make check` fails. | `test_tamper_manifest.py` |
| `tools/tests_pii` runs under the T-2 safety net. | `test_safety_net_suites.py` (ROOT_SUITES), `tools/tests_pii/test_safety_net_guard.py` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-PII @ MS-6 | compose, 2026-11-13 | pass | docs/evidence/SEC-1/pii-scan-report.md | yes (last prover) |
| NF-PII-INT @ MS-6 | compose, 2026-11-13 | pass | docs/evidence/SEC-1/tamper-report.md | yes (last prover) |
| SM-1 @ MS-6 | compose, 2026-11-13 | contributes: Jaeger traces of a full run hold no PII (826 spans scanned) | pii-scan-report.md | no (DEMO-1 proves it on the VM) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | Every step through `test` passed: plan ok, tooling `221 passed`, lint/typecheck/test for 17 projects (re-run at HEAD: `make plan-check env-check tooling-test lint typecheck test` exit 0). `test-xt` failed twice on the known flake: run 1 `352 passed, 2 errors`, run 2 `351 passed, 3 errors`. All errors were setup-only `ServerSelectionTimeoutError … connection closed`, on different cases each time, while three other compose stacks were up. Re-running those 5 cases gave `5 passed`; `nx run-many -t test-xt` (mcp-hub) succeeded. The diff touches no route, tool or consumer. |
| PII scan | `make test-pii` | exit 0: `hits=0`, `231/231 rejected`, `4 passed in 47.31s` |
| Mutation | `uv run --frozen python docs/evidence/SEC-1/mutate.py` | `14/14 as expected` |
| Integration, contracts, evals | n/a | Test tooling only: no app, lib, contract, prompt or manifest changed. |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| OBS-1 | spans and logs are scrubbed before export, by default | yes: C1 is clean, while P4 and P5 (scrubbers off) leak |
| PII-3 | detectors catch mobile, PAN, Aadhaar, email, IFSC, and an account number with a keyword | yes; names cannot be detected (see open findings) |
| AGT-1 / RUN-3 / HIL-1 / CHAT-1 | the seed template runs end to end with agents through the hub; approvals resume; the chat redacts | yes: the full run completed in the scan |
| F-2 / OBS-1 tunnel pattern | socat tunnels to Mongo, the collector and Jaeger | yes. Jaeger 2.23 serves only the v3 query API (`/api/v3/traces`) |

## Mutation checks
`docs/evidence/SEC-1/mutation-checks.txt`. P1–P6 are production leaks or scan gaps (KILLED by `test_a_full_run_leaves_no_raw_pii_outside_the_vault`). C1 is the control (clean as expected). S1–S6 are scanner and tamper-report guards (KILLED by the unit tests named in the file). T1 breaks the vault signature check (KILLED by `test_every_tamper_case_is_rejected`).

## Review findings left open
Two independent reviews (code and security) found no critical issues; fixed: scenario breadth, API responses, key-safe locations, opaque-id rule, `--since`, tamper timeout and failure detail, OTEL env restore, PII read-only cases, mutant reports in a scratch folder.
- **HIGH (PRD question for Haneef)** · `onb_guardrails.pii` · An account-holder **name** (or any name) typed into free text (notes, chat, reveal reason) is not detectable: PRD §7.10 lists only mobile, PAN, Aadhaar, IFSC, email and account-number detectors. So the zero-hit claim covers names only where they enter through `pii.*` (vault). The scenario puts every detectable type into free text, and a bank account number with a keyword is caught. A bare account number with no keyword is not (PII-3 rule). Decide: accept and document, or add a name or free-text policy.
- MEDIUM · scan covers final state only (not the oplog); a value written then overwritten is missed.
- MEDIUM · encoded forms (base64 of a value) are not searched.
- LOW · only the happy path plus one 422 is exercised: no Telegram webhook, retention purge or rejected-grant path in the full run (rejected grants are in the tamper report).
- LOW · `stack.py` does not close the per-service Mongo clients or the background loop (test process exits); `free_port` race; `bcrypt` reached through `onb-api`'s dependencies for `tools/ci_env.py`.
- LOW · a few manifest entries prove fail-closed properties rather than refusal of a crafted input (e.g. `test_the_signature_covers_every_field_but_itself…`), so the count includes them.

## Left undone, and why
- Box 4 has no hosted CI run: there is no GitHub remote (F-3 on hold), and pushing is not allowed.

## Found for other goals (sweep of earlier open findings)
- **OBS-1 / RUN-3 · over-redaction of ids** (hex ids and a 10-digit run id → `[MOBILE]`): not a leak. The scan now counts `markers_glued_into_ids_by_source`; it was `{}` in the final run. Owner **SEC-2**: an id-aware exemption in the scrubber if it shows up.
- **AGT-1 · templates_agent reads whole `new_hires` documents**: not a leak today. Notes are redacted and `pii.*` holds tokens; P2 shows that unredacted notes would leak into Mongo and API responses, and the agent runtime's redaction still kept them out of LLM inputs. Owner **SEC-2**: hub-side field projection (least privilege).
- **PII-5 · backups not purged**: outside what the suite can see. Owner **DEP-1**: backup retention ≤ 90 days or crypto-shredding of org keys (ADR 0023).
- **CHAT-1 · self-review only**: its PII path is now covered by `make test-pii`. The stored question is redacted, and the model sees `[MOBILE]`, not the value (P3 proves the check bites). Owner **SEC-2** for the rest (rate limits, reading all users' messages).
- **F-3**: `ci.yml` here = F-3's file + `pii` job. When F-3 merges, keep this version (or add the job to F-3's); F-3's `tools/tests/test_ci_workflow.py` may assert the job list.

## Decisions forced (ADRs written)
None (ADR 0028 not needed).

## Traps hit
- Jaeger 2 has no v1 `/api/traces`; use `/api/v3/traces?query.service_name=…&query.start_time_min=…`.
- `docker compose logs` without `--since` returns the whole history: earlier sessions' lines piled up.
- The pytest failure repr of a dataclass printed the Mongo root URI; fields are now `repr=False`.
- The `test-xt` Mongo "connection closed" flake hit twice under memory pressure (other stacks up).
