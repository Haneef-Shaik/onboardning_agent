# Handoff — PII-3 · Validate, detect, mask and blind-index sensitive values        attempt 1 · goal/pii-3-pii-detectors-masks · see `git log -1` (code at 8bbd54a; evidence refreshed after it)

**Outcome claimed.** The branch adds pure functions for each sensitive field of PRD §7.10:
- **Validators** (`onb_guardrails.pii`): Aadhaar with Verhoeff, PAN, IFSC, mobile, email, bank account.
- **Free-text detectors** (`detect` returns typed spans; `redact` replaces them with `[PAN]`-style markers).
- **Display masks** (`onb_crypto.mask`), exactly the PRD strings.
- **Blind indexes** (`onb_crypto.blind_index`): HMAC-SHA256 with a caller-supplied per-org key.

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Each PRD mask example produces exactly the PRD string (table test) | yes | `test_each_prd_mask_example_produces_exactly_the_prd_string[mobile/email/aadhaar/pan/bank_account]` and `test_the_prd_table_covers_every_masked_field` in `libs/py-crypto/tests/test_masks.py`. Command: `uv run --frozen pytest libs/py-crypto/tests/test_masks.py -v`. Result: `6 passed` (`docs/evidence/PII-3/mask-table.txt`). The outputs are `98XXXXXX21`, `j***@domain.com`, `XXXX XXXX 1234`, `ABXXXXX4F`, `XXXXXX6789`. |
| Verhoeff valid and invalid vectors pass; Aadhaar starting with 0 or 1 is rejected | yes | `libs/py-guardrails/tests/pii/test_validators.py` covers four things: (1) `test_verhoeff_accepts_valid_vectors[*]` and `test_verhoeff_rejects_invalid_vectors[*]`; (2) `test_verhoeff_check_digit_matches_vectors`; (3) every single-digit error and every adjacent transposition is caught; (4) `test_aadhaar_starting_with_0_or_1_is_rejected[*]`. The cases in (4) carry a correct Verhoeff check digit, so only the 0/1 rule rejects them. Every vector was cross-checked against **python-stdnum**, an independent implementation: there are 0 mismatches on the vectors and on 10,000 random numbers (`docs/evidence/PII-3/verhoeff-oracle.txt`). |
| A corpus of 50+ synthetic positives and negatives has zero false negatives; the false-positive rate is recorded in the handoff | yes | `libs/py-guardrails/tests/pii/corpus.jsonl` has 82 cases: 50 positive, 32 negative. `test_corpus_has_zero_false_negatives` passes, and so does `test_corpus_false_positive_rate_is_measured`, which pins the measured set. **False-positive rate: 2/32 = 6.2%.** Both are deliberate hard negatives that no regex can tell from PII: a 10-digit order id starting with 9 (`neg-hard-order-10-digits`) and an IFSC-shaped SKU (`neg-hard-ifsc-shaped-word`). The other 30 negatives give 0 false positives. They include 12-digit order ids that fail Verhoeff, Aadhaar-shaped numbers starting 0/1, pincodes, timestamps, `ptk_` tokens and ISBNs. Output: `docs/evidence/PII-3/corpus-fp-rate.txt`. |
| A property test shows `redact()` output contains no detector hit | yes | `libs/py-guardrails/tests/pii/test_redact_property.py` (hypothesis, 1500 examples each, 5 properties, all pass; `docs/evidence/PII-3/redact-property.txt`). `test_redact_output_contains_no_detector_hit` is the card's property. Because `redact()` loops to a fix point, I added two properties that a partial leak cannot pass: `test_no_character_of_a_value_survives_in_any_glue` and `test_digit_values_glued_to_letters_leave_nothing_behind`. They use glued and obfuscated values, and assert that no digit or `@` from the value survives. These properties have teeth: (a) they fail on the attempt-1 detectors (`red-property-partial-leaks.txt`); (b) with the fix-point loop removed, the card's property fails on its own and finds `200000000009AAAAA0000A` (`property-kills-single-pass.txt`). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `from onb_guardrails.pii import detect, redact, PiiType, PiiSpan` behaves as follows. `detect(text)` returns non-overlapping `PiiSpan(type, start, end)` in text order, with offsets into the **original** text. Spans never carry the value, so they are safe to log. | `test_spans_are_frozen_ordered_and_carry_no_value` |
| `redact(text)` replaces each span with `PiiType.marker` (`[AADHAAR]`, `[PAN]`, `[IFSC]`, `[MOBILE]`, `[EMAIL]`, `[BANK_ACCOUNT]`). It is idempotent, and text without digits or `@` comes back unchanged. | `test_redact_replaces_every_value_with_its_typed_marker`, properties |
| Detection survives the following tricks: zero-width characters, soft hyphens, any Unicode space, Hangul and Braille blanks, full-width, superscript and circled forms, non-ASCII digits, and Cyrillic and Greek lookalikes. Digit groups may be separated by any 0–3 non-alphanumeric characters (Unicode minus, dashes, `_`, `,`, ` - `). Mobiles may be grouped 5-5, 3-3-4, 4-3-3, 4-4-2, 2-3-2-3 or 2×5, with a `+91`, `0091`, `91` or `0` prefix. Also covered: a separated or glued upper-case PAN, IDN and `%40` emails, and over-long email local parts. | `test_formatting_tricks_do_not_hide_a_value[*]` (27), `test_more_separator_variants_do_not_hide_a_value[*]` (13), `test_email_walk_back_and_bank_gaps[*]` |
| Runtime is linear. Adversarial 100–260 KB inputs each finish in under 5 s: account keyword + 100k spaces, 20k PANs, 200k digits, and 50k chars + 10k `@a.co` / 30k `%40a.co`. | `test_redact_stays_linear_on_review_inputs[*]`, `test_email_walk_back_is_linear[*]`, `test_redact_is_linear_on_adversarial_input` |
| `ptk_…` tokens are never detected, so tokens can flow through redaction. | `test_pii_tokens_are_not_detected[*]` |
| Validators: `is_valid_aadhaar/pan/ifsc/mobile/email/bank_account` and `verhoeff_is_valid/verhoeff_check_digit`. All are pure and total. Aadhaar input accepts only `NNNNNNNNNNNN`, `NNNN NNNN NNNN` and `NNNN-NNNN-NNNN`. | `test_validators.py` |
| `onb_crypto.mask(MaskedField.X, value)` and `mask_*`. Masks are fixed width, so a mask never reveals length. A malformed value raises `ValueError("malformed <field> value")`, and the message never contains the value. | `test_masks.py`, M18 |
| `onb_crypto.blind_index(org_key, BlindIndexField.X, value)` returns 64 hex chars: HMAC-SHA256 over `field\x1fcanonical`. Key ≥ 32 bytes. Layouts of one value share an index. Indexes differ per field and per org key. Emails must be plain ASCII. `BlindIndexField.X.index_name` returns `email_hash`, `mobile_hash`, `pan_hash` and `aadhaar_hash`. | `test_blind_index.py` (known-answer digests computed independently with `openssl dgst -hmac`) |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| — | — | — | `plan.py show PII-3` lists no ledger rows for this goal | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` (at 8bbd54a) | `make check: all passed`. There are 17/17 lint, typecheck and test runs, and every `libs/py-*` reports `Total coverage: 100.00%`. py-guardrails ran 179 tests and py-crypto 53. Summary: `docs/evidence/PII-3/make-check.txt` |
| Integration | `make test-int` | `No tasks were run`. This branch was cut from 820f473, before F-2 (42301cc) added the `test-int` targets, and the orchestration rules forbid a rebase. The diff adds no DB, compose or IO code; py-crypto only gains pure functions. **The orchestrator should run `make test-int` after integrating onto main.** |
| Eval self-test | `make evals-selftest` | Not available yet (EVAL-1 is not done) |
| Others | test-xt, contracts-check, test-pii, e2e | Not created yet, and the diff adds no route, contract or UI |

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| F-1 | Libs import each other as `onb_<name>`; a new dev dependency goes in the root `pyproject.toml` dev group, then `uv lock`. | yes: `hypothesis` was added to the dev group and `uv.lock` was regenerated |
| F-1 | `make check` enforces 80% line coverage per lib | yes: the gate ran (100% on both libs) |
| F-1 | Tooling note: use `node_modules/.bin/nx` or `rtk proxy …` because the rtk hook rewrites commands | yes: it also truncates `make check` output, so evidence was captured with `rtk proxy make check` |

## Mutation checks
29 mutations; every one was killed by a named test, and the suite is clean afterwards (`after restore, full suite: 232 passed`, `git diff --quiet libs: True`). Full output: `docs/evidence/PII-3/mutation-checks.txt`. Bytecode was purged around every mutation (see Traps). Each line gives the guard, how it was broken, and the test that failed.
- M1 Aadhaar 0/1 rule removed → `test_aadhaar_starting_with_0_or_1_is_rejected[023456789014]`
- M2 Verhoeff dropped from Aadhaar → `test_aadhaar_rejects_bad_numbers[999900000126]`
- M3 one swap in the base permutation → `test_verhoeff_accepts_valid_vectors[999900000125]`
- M4 `redact()` single pass → `test_redact_repeats_until_a_value_exposed_by_redaction_is_gone`
- M5 MOBILE ranked above AADHAAR → `test_mobile_inside_a_12_digit_aadhaar_is_reported_as_aadhaar_only`
- M6 mobile digit boundaries dropped → `test_a_12_digit_number_failing_verhoeff_is_not_aadhaar`
- M7 Aadhaar candidates non-overlapping → `test_aadhaar_after_a_failing_group_is_still_found`
- M8 Unicode digit folding off → `test_unicode_digits_cannot_hide_a_value`
- M9 PAN case-sensitive → `test_each_detector_returns_a_typed_span_over_the_value[pan abcde1234f-…]`
- M10 bank keyword anchor optional → `test_corpus_has_zero_false_negatives`
- M11–M14 PAN / mobile / Aadhaar / email mask changed → `test_each_prd_mask_example_produces_exactly_the_prd_string[pan|mobile|aadhaar|email-…]`
- M15 blind index without field separation → `test_blind_index_matches_independent_hmac_sha256[mobile-…]`
- M16 key length check removed → `test_short_keys_are_rejected[]`
- M17 email not lower-cased → `test_layouts_of_one_value_share_an_index_so_dedupe_works[email-…]`
- M18 error echoes the value → `test_mask_rejects_malformed_input_without_echoing_it[mask_mobile-…]`
- M19 zero-width chars kept → `test_formatting_tricks_do_not_hide_a_value[PAN ABCDE​1234F-…]`
- M20 homoglyph folding off → `test_formatting_tricks_do_not_hide_a_value[PAN <Cyrillic>…]`
- M21 overlap merge keeps first span only → `test_overlapping_values_are_redacted_as_their_union`
- M22 no email left-extension → `test_formatting_tricks_do_not_hide_a_value[xxx…@example.com-[EMAIL]]`
- M23 bank gaps unbounded (the quadratic pattern) → `test_redact_stays_linear_on_review_inputs[account-spaces]`
- M24 TLD may not precede `-` → `test_formatting_tricks_do_not_hide_a_value[jane@example.com-call-…]`
- M25 3-3-4 mobile grouping removed → `test_formatting_tricks_do_not_hide_a_value[call 900 000 0001-…]`
- M26 email canonical form loosened → `test_email_index_accepts_only_plain_ascii_addresses[Kte@example.com]`
- M27 email walk-back ignores the previous match → `test_email_walk_back_is_linear[url-encoded]`
- M28 email walk-back crosses `@` and `/` → `test_email_walk_back_and_bank_gaps[see http://site/path/jane@example.com-…]`
- M29 separators narrowed back to space/dot/hyphen → `test_formatting_tricks_do_not_hide_a_value[id 9999\n0000\n0125-…]`

RED before GREEN is recorded in `red-validators.txt`, `red-detectors.txt`, `red-masks-blind-index.txt` (collection errors before implementation), `red-review-findings.txt` (29 of 33 review-finding tests fail on the attempt-1 detectors), `red-property-partial-leaks.txt` and `red-re-review.txt` (16 re-review tests fail at 996b334).

## Review findings left open
code-reviewer and security-reviewer both ran (general-purpose agents with reviewer briefs). All their critical/high findings are fixed in 6c66788: ReDoS in the bank pattern, the email-hyphen escape, partial leaks from dropped overlaps, and formatting bypasses. So are the mediums: 64-char email prefix leak, quadratic span selection, weak property tests, realistic-looking fixtures, and the Unicode email index collision. Both reviewers re-checked at 996b334. They confirmed every finding fixed, and both reported one new high: the email walk-back was quadratic (`"x"*50000 + "@a.co"*10000` took 14 s). The security reviewer also listed more separator variants. All of it is fixed in e3da8d3 and 8bbd54a: the walk stops at `@`, `/` and the previous match's end; gaps accept any short non-alphanumeric run; and there are more mobile groupings. The URL over-redaction (`http:[EMAIL]`) is fixed too, and so is the newline-after-keyword bank regression. Left open:
- low · `detectors.py` `_BANK_ACCOUNT` · A bare account number with no keyword (`send to 50100000000001`) is not redacted. Any 9–18 digit run is too common to flag by default (amounts, ids, AWBs). Possible follow-up for GR-1: redact long digit runs near an IFSC.
- low · `detectors.py` `_PAN_*` · Two PAN forms are missed: a PAN glued to *preceding* letters (`PANABCDE1234F`, or after a symbol that NFKC-folds to a letter, like a circled A), and a spaced lower-case PAN (`abcde 1234 f`). Leetspeak and "[at]"/"dot" spellings are not handled either. All of these are residual risk and should be documented in GR-1.
- low · `detectors.py` `_MOBILE` · For `+91 098765 43210` only the country prefix `+91 ` survives. The number itself is redacted.
- low · `blind_index.py` · Key rotation (a key-version prefix) and a separate Aadhaar key type belong to PII-2's key management. The docstring says `aadhaar_hash` must use the Aadhaar partition key.
- low · `normalize.py` · Masks and indexes check shape only (no Verhoeff, and a bare `91` prefix is accepted). Callers validate on input first, as the docstring says.

## Left undone, and why
- The `/goal` condition could not be set from this orchestrated background session. I worked to the same condition text (`plan.py goal PII-3`).
- `make test-int` could not exercise anything on this branch base (see Gates).

## Found for other goals
- **PII-2:** Pass the per-org *Aadhaar* key for `BlindIndexField.AADHAAR` (PRD §7.10) and consider versioned index keys. `blind_index` rejects keys under 32 bytes.
- **GR-1 / OBS-1:** Use `redact()` as-is; offsets map to the original text. Document the residual risks listed above. For telemetry, `detect()` spans are safe to log.
- **HIRE-1:** Validate with `is_valid_*` before calling `mask`/`blind_index`. An Aadhaar must pass `is_valid_aadhaar` (Verhoeff) on input.
- **SEC-1 (`make test-pii`):** The PII-3 test fixtures (`libs/py-guardrails/tests/pii/corpus.jsonl` and the test modules) contain format-valid synthetic values (`9999…` Aadhaars, the `90000` mobile range, `example.com`). The PII scan will need to allowlist them.

## Decisions forced (ADRs written)
None needing an ADR. Smaller decisions, all within the PRD:
- **PAN mask.** It is `ABXXXXX4F` (9 characters for a 10-character PAN), exactly as the PRD writes it. The account mask also has a fixed width. Possible PRD typo, flagged here, not changed.
- **Bank account detector.** It needs an account keyword. The PRD's free-text list has no bank account, but the card's scope names one.
- **Overlap typing.** Overlaps are typed by the longest candidate, ties broken by priority. That keeps a Verhoeff-valid `91XXXXXXXXXX` as Aadhaar.
- **One root dev dependency.** `hypothesis` was added to the root dev group (for the property test).

## Traps hit
- **Wrong Verhoeff table rows.** I mistyped two rows when transcribing the table, and the tests built on my own vectors passed anyway. The python-stdnum cross-check caught it. The rows are now derived from the base permutation, and `334249855766` stays as a regression vector.
- **Stale bytecode in mutation runs.** A same-size edit within one second keeps the old `.pyc` valid (mtime+size check), so a "restored" file kept running mutated bytecode. The mutation runner now sets `PYTHONDONTWRITEBYTECODE=1` and purges `__pycache__`.
- **Escapes decoded by the editor.** `\uXXXX` escapes typed into the editor tool were written as literal characters, which ruff flagged as RUF001. Non-ASCII characters in the sources are now escaped.
- **Card traps.** Both are covered by named tests: `test_a_12_digit_number_failing_verhoeff_is_not_aadhaar` and `test_mobile_inside_a_12_digit_aadhaar_is_reported_as_aadhaar_only`.
