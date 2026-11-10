# Handoff — OBS-1 · Scrub PII from every span and log before export        attempt 1 · goal/obs-1-redaction-processor · code at 12080be (see `git log -1` for the handoff commit)

**Outcome claimed.** `init_telemetry` now scrubs every span and every log record with the PII-3 detectors before they leave the process:
- **Spans:** `onb_telemetry.span_scrub.PiiScrubbingSpanProcessor` is the pipeline's fixed `final_hook`, after every registered hook and right before export.
- **Logs:** `onb_telemetry.log_scrub` patches `Logger.makeRecord` and adds a filter to the root handlers.
- **Tokens:** raw values become `[MOBILE]`/`[PAN]`/`[EMAIL]`/… markers, and `ptk_` tokens pass unchanged.
- **Failure:** both fail closed without raising into the caller.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| A span with a mobile number, PAN and email in attributes and events exports with markers only (in-memory exporter test) | yes | **Test:** `libs/py-telemetry/tests/test_pii_scrubbing.py::test_span_with_pii_in_attributes_and_events_exports_with_markers_only` PASSED. The span goes through `init_telemetry` with `InMemorySpanExporter` (the `telemetry` fixture). It asserts the exact marker attributes, the event attributes and the event name. It also asserts that no fragment of any raw value (`98765`, `43210`, the PAN, the email, `jane.doe`, `example.com`) is in `span.to_json()`.<br>**Other span fields:** `test_pii_in_every_other_span_field_is_scrubbed` covers the name, keys, int and sequence values, the status, the exception event and links. `test_resource_and_scope_attributes_are_scrubbed` covers the resource and scope.<br>**Command:** `uv run --frozen pytest libs/py-telemetry/tests/test_pii_scrubbing.py -v` gives `38 passed` (`docs/evidence/OBS-1/pii-scrubbing-tests.txt`). |
| A `ptk_` token passes through unchanged (test) | yes | `test_a_pii_token_passes_through_a_span_unchanged`: name, attribute and event attribute are byte-equal. `test_a_pii_token_passes_through_a_log_record_unchanged` covers the message and an extra. `test_a_token_next_to_a_raw_value_keeps_the_token_and_loses_the_value` gives `"ptk_… [PAN]"`. All PASSED (same file). |
| A log record carrying the same values is scrubbed (test) | yes | **Test:** `test_a_log_record_carrying_the_same_values_is_scrubbed` PASSED. The record carries the mobile, PAN and email in the template, the args, an `extra=` field, an exception and `stack_info`. The formatted line contains only markers and no raw fragment. `exc_info` is dropped, and `exc_text` is the redacted traceback.<br>**Related tests:** `test_the_log_filter_hands_on_a_scrubbed_copy`, `test_non_string_extra_fields_are_scrubbed_and_harmless_ones_kept`, `test_extra_fields_are_scrubbed_for_a_handler_added_after_init`, `test_a_value_split_between_template_and_argument_is_still_scrubbed`, `test_a_formatter_that_unpacks_args_keeps_working` (uvicorn AccessFormatter shape). |
| `init_telemetry` installs the processor by default, asserted by a test | yes | `test_init_telemetry_installs_the_pii_span_processor_by_default` calls a plain `init_telemetry("svc", exporter=…)` with no processors. It asserts that `handle.pipeline.final_hook` is a `PiiScrubbingSpanProcessor` and that an exported span has markers. `test_init_telemetry_installs_log_scrubbing_by_default` (caplog) and `test_log_scrubbing_outlives_shutdown_and_only_the_test_reset_removes_it` cover logs. All PASSED. Mutations M1 and M33 (no default install) are killed. |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| Every service gets span and log scrubbing just by calling `init_telemetry(...)`. There is no switch to turn it off. | box 4 tests; M1, M33 |
| The scrubber is the **last** stage before export. A hook (`processors=` / `register_span_processor`) sees raw spans and cannot re-add PII to what is exported. | `test_scrubbing_runs_after_every_hook_so_a_hook_cannot_reintroduce_pii`; M2 |
| A span whose scrubbing raises is dropped, never exported raw. | `test_a_failing_scrubber_drops_the_span_rather_than_export_it_raw`; M12 |
| Log scrubbing covers every logger, non-propagating ones and handlers added later included, `extra=` fields of any type, exception text and `stack_info`. It stays on after `handle.shutdown()`. | log tests; M18–M34 |
| If log scrubbing fails, the record is replaced by `"[log record withheld: PII scrubbing failed with <Type>]"` (from makeRecord) or dropped (by the filter). The caller never gets an exception. | `test_a_failing_log_scrubber_withholds_…`, `test_a_failing_log_filter_drops_the_record` |
| Scrubbed records keep `args` (scrubbed) when safe, so formatters that read `record.args` (uvicorn access log) work. | `test_a_formatter_that_unpacks_args_keeps_working`; M22 |
| A value under an account-like key (`bank_account`, `acct`, `acc_no`, `accountNumber`, `iban`) is scrubbed as a bank account. A bare 9–18 digit number elsewhere is not, which is PII-3's rule. | `test_a_bare_account_number_under_an_account_key_is_scrubbed[*]`, `test_numbers_under_other_keys_are_not_mistaken_for_accounts`; M16 |
| A W3C hex id under `trace_id`/`span_id`/`otelTraceID`/`otelSpanID` is kept intact, but a PII value under those keys is still scrubbed. | `test_trace_correlation_ids_are_not_mangled[*]`, `test_a_mobile_under_a_trace_id_key_is_still_scrubbed`; M17 |
| `onb_telemetry.testing.reset_telemetry_globals()` also restores logging (`setup.uninstall_log_redaction_for_tests`). Services never call it. | `test_log_scrubbing_outlives_shutdown_…`; M32 |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| NF-PII @ MS-6 | compose (unit + `make test-int`), 2026-11-10 | contributes: spans and logs are scrubbed in-process | this handoff, `docs/evidence/OBS-1/` | no (not the last prover) |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `rtk proxy make check` | `make check: all passed` (`docs/evidence/OBS-1/make-check.txt`) |
| Coverage | `npx nx run py-telemetry:test --skip-nx-cache` | `TOTAL 575 14 98%`, `Total coverage: 97.57%`, `115 passed` (`py-telemetry-coverage.txt`); new modules 96–99% |
| Integration | `rtk proxy make test-int` (compose project `ob-obs-1`) | `12 passed`, `24 passed`, `3 passed` (py-telemetry Jaeger round-trip included); `Successfully ran target test-int for 2 projects` (`make-test-int.txt`) |
| Cross-tenant, contracts, evals, PII scan, e2e | n/a | The diff adds no route, contract, agent or UI. `make test-pii` does not exist yet (SEC-1). |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-4 | Subclass `RedactingSpanProcessor`, return a `copy_span`; hooks run before export; a raise in `on_end` drops the span | yes. I added an optional `final_hook` to `HookedSpanProcessor` (it runs after every hook) instead of registering a normal hook, so later hooks cannot undo scrubbing. |
| F-4 | Install redaction via init so no span ends before it is in place | yes: built into `init_telemetry` |
| F-4 | `testing.captured_telemetry` / `telemetry` fixture with in-memory exporter | yes |
| PII-3 | `redact()` replaces values with typed markers, is idempotent, never matches `ptk_`, and is linear | yes. Bare bank account numbers need a keyword, which is handled through the key context. |

## Mutation checks
34 mutations, all killed. Source restored clean, and the suite gives `115 passed` after the restore. Script: `docs/evidence/OBS-1/mutations.py`. Output: `docs/evidence/OBS-1/mutation-checks.txt`. Each line shows the guard that was broken and the test that failed:
- M1 init installs no scrubber → `test_span_with_pii_in_attributes_and_events_exports_with_markers_only`
- M2 scrubber before hooks → `test_scrubbing_runs_after_every_hook_so_a_hook_cannot_reintroduce_pii`
- M3/M4 attribute values/keys → `…markers_only` / `test_pii_in_every_other_span_field_is_scrubbed`
- M5/M6 event attributes/names → `…markers_only`
- M7–M11 span name, links, status, numbers, mixed sequences → `test_pii_in_every_other_span_field_is_scrubbed`
- M12 failing hook continues → `test_a_failing_scrubber_drops_the_span_rather_than_export_it_raw`
- M13/M14 scope/resource → `test_resource_and_scope_attributes_are_scrubbed`
- M15 dropped count → `test_dropped_attribute_counts_survive_scrubbing`
- M16 account-key context → `test_a_bare_account_number_under_an_account_key_is_scrubbed[bank_account]`
- M17 trace-id exemption for any value → `test_a_mobile_under_a_trace_id_key_is_still_scrubbed`
- M18/M19 nested mappings/bytes → `test_non_string_extra_fields_are_scrubbed_and_harmless_ones_kept`
- M20/M21 message not redacted / split value → `test_a_value_split_between_template_and_argument_is_still_scrubbed`
- M22–M26 args cleared, extras, exc_info kept, exc text, stack text → `test_a_log_record_carrying_the_same_values_is_scrubbed`
- M27, M31, M32, M34 makeRecord not patched, no root filter, reset doesn't restore, shutdown uninstalls → `test_log_scrubbing_outlives_shutdown_and_only_the_test_reset_removes_it`
- M28/M29 raw record on failure, withheld loses fields → `test_a_failing_log_scrubber_withholds_the_record_without_breaking_the_caller`
- M30 filter fails open → `test_a_failing_log_filter_drops_the_record`
- M33 no log install → `test_init_telemetry_installs_log_scrubbing_by_default`

RED before GREEN:
- `red-scrubbing.txt`: collection error before the modules existed.
- `red-review-findings.txt`: 15 review-finding tests failing before the fix commit.

## Review findings left open
A code reviewer and a PII-focused security reviewer ran on `git diff main...HEAD` (both as subagents). Two HIGH issues, one from each reviewer, were fixed in 12080be: non-str extras, and args being cleared, which broke uvicorn. These MEDIUM issues were fixed too: shutdown disabling log scrubbing, bare account numbers, resource attributes, and trace-id mangling. These LOW issues were also fixed: instrumentation scope, dropped counts, withheld placeholders, extra-field names, and the double scrub in the root filter. The following are still open:
- **medium · `span_scrub.py`, `log_scrub.py` · cost on the hot path.** Scrubbing runs on the thread that ends the span or makes the record, and the message is formatted eagerly. Keys are cached (`lru_cache`), and records already scrubbed skip the root filter. Moving span scrubbing to the batch worker would need a scrubbing exporter wrapper. That is left until a latency budget says it matters.
- **medium · `scrub_values.py` · false positives by design.** A 10-digit number starting with 6–9, or a 12-digit Verhoeff-valid number, is scrubbed even when it is a count or an id. For example, `db.rows=7000000000` becomes `"[MOBILE]"`, and the attribute type changes. This is pinned and documented by `test_a_ten_digit_number_starting_6_to_9_is_treated_as_a_mobile_by_design`. Losing a metric is preferred to leaking a value.
- **medium · `log_scrub.py` · `exc_info` is dropped.** It carries the raw exception message. Handlers that read `exc_info` (OTel log export, Sentry) get only `exc_text`. Whoever adds OTel log export should map `exc_text` and the type name.
- **low · `span_scrub.py` · a link's `tracestate` is not scrubbed.** It is vendor key/values from an incoming header. This is documented in the module docstring.
- **low · `scrub_values.py` · redacted keys can collide.** Two email-named keys both become `[EMAIL]` and one value wins. Data is lost, but no PII leaks.
- **low · `log_scrub.py` · backstop filter coverage.** The backstop filter is only on root handlers that exist at init. A record that bypasses `Logger.makeRecord` (`logging.makeLogRecord`, a Logger subclass overriding it, a QueueListener replay) and reaches a handler added later is unscrubbed. Documented: configure logging before `init_telemetry`, or add `PiiScrubbingLogFilter`.
- **low · `setup.py` · `TelemetryHandle.provider` is public** (carried from F-4). `provider.add_span_processor(...)` would export past the scrubber. This is documented only.
- **low · `print()`, `sys.excepthook` and `threading.excepthook` write raw tracebacks to stderr** and are not covered. See "Found for other goals".
- **low · `dropped_events` and `dropped_links` counts are not carried over;** only `dropped_attributes` is.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. I worked to the text printed by `plan.py goal OBS-1`.
- No content-capture backstop (stripping `input.value`/`gen_ai.*` when capture is off). F-4's switch already defaults it off, and every such value is PII-scrubbed here anyway. Building it belongs with LLM-1's instrumentor.

## Found for other goals
- **SEC-1 (`make test-pii`):**
  - Grep compose logs and Jaeger for the fixture values. This lib scrubs in-process only.
  - Consider routing `sys.excepthook` and `threading.excepthook` through a logger so uncaught tracebacks are scrubbed.
  - Consider a lint that forbids `.provider.add_span_processor`.
- **C-1 / every app service:** configure logging (dictConfig, uvicorn `log_config`) **before** `init_telemetry`, so the backstop filter lands on the real root handlers. The makeRecord patch covers everything else regardless. Test fixtures must use `onb_telemetry.testing` (its reset restores logging).
- **LLM-1:** OpenInference content attributes are scrubbed too, but tokens are kept, so a prompt carrying only `ptk_` values passes intact.
- **Whoever adds OTel log export:** records have `exc_info=None` and a redacted `exc_text`. Map `exc_text` to `exception.stacktrace`.

## Decisions forced (ADRs written)
None needed an ADR. Recorded in module docstrings:
- **Scrubber placement:** the scrubber is a fixed final pipeline stage, not a normal hook (`pipeline.py`).
- **Log hook point:** logs are scrubbed by patching `Logger.makeRecord` rather than the record factory, because `extra=` fields are added after the factory runs.
- **Lifetime:** log scrubbing lives for the whole process.
- **Number trade-off:** a mobile-shaped number becomes its marker string (`scrub_values.py`).

## Traps hit
- `logging.makeLogRecord` calls the current record factory. Copying a record through it inside a scrubbing factory recursed, so records are copied with `copy.copy`.
- `Logger.makeRecord` sets `extra=` fields after the record factory runs, so a factory never sees them.
- PII-3's email detector takes `_ . ? =` into the local part. `hire_<email>` and `?email=<email>` become `[EMAIL]` as a whole, which over-redacts. That is safe.
- `Span.attributes` is a mapping proxy without `dropped`. Read `span.dropped_attributes`.
- pytest's caplog handler is on the root logger at init time, so it gets the backstop filter. A test of the makeRecord path alone needs a non-propagating logger.
- The rtk hook rewrites `git`/`grep`. Compound shell commands that mention git are refused by the worktree guard, so run them as separate calls.
