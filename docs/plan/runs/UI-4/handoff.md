# Handoff — UI-4 · New hires with PII entry, masking and audited reveal        attempt 2 · goal/ui-4-hires-ui · see `git log -1` (handoff commit)

**Outcome claimed.** HR/HR_ADMIN add a hire with sensitive fields, see every one masked (`MaskedField`), reveal one with a mandatory reason for the API's TTL (30 s) after which it hides, and upload scans whose metadata (type, upload time, verified flag) is listed. Managers have no Hires section and no reveal control; HR has no Aadhaar reveal control (HR_ADMIN does).

Code: `apps/web/src/hires/` (`policy.ts`, `useReveal.ts`, `hires-api.ts`, `new-hire.ts`, `NewHireForm`, `HiresPage`, `HireDetail`, `DocumentsPanel`), wired in `App.tsx` as a `/hires/*` route inside UI-8's `RequireSection` (the same nav-table check as `/admin/*`; `Guarded` is back to main's form). API (the one addition the card needed, per the orchestrator's note): `GET /hires/{hire_id}/documents`, metadata only, HR/HR_ADMIN (`documents/routes.py`, `service.list_for_hire`, `meta.list_for_hire`, contract `DocumentsEnvelope`). The Playwright API server gets the hire and document services from UI-8's `admin_seed.admin_services` (`apps/web/e2e/admin_seed.py` `hires()` now also builds the real `DocumentService` over the same store, keyring and audit log; `serve_api.py` is main's). No ADR was needed (0017 unused).

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| Playwright: HR creates a hire, sees masked fields, reveals one with a reason, and it hides again after the TTL | yes | `apps/web/e2e/hires.spec.ts › HR creates a hire, sees masked fields, reveals one with a reason, and it hides again after the TTL`. Signs in as HR, fills the add-hire form, lands on the hire, asserts 7 `masked-field`s all `data-state=masked`, none of the typed raw values or `ptk_` on the page; Confirm is disabled until a reason is typed; the revealed mobile shows, only that one field is revealed; with `page.clock`, at TTL−2 s it is still revealed and at 30 s it is masked again and the raw value is gone. `E2E_API_PORT=19810 E2E_WEB_PORT=19811 npx playwright test --config apps/web/playwright.config.ts` → `8 passed` (`docs/evidence/UI-4/playwright-direct.txt`). Unit: `useReveal.test.tsx` (8), `HireDetail.test.tsx` (reveal with reason, cancel, refusal). |
| Playwright: a manager sees no reveal control; HR sees no Aadhaar reveal control | yes | `hires.spec.ts › a manager sees no reveal control; HR sees no Aadhaar reveal control`: HR → 0 `Reveal Aadhaar`, exactly 6 `Reveal …` buttons; HR_ADMIN → 7 including Aadhaar; MANAGER → no Hires nav link, `/hires` and `/hires/<id>` show "Not available", 0 reveal buttons and 0 masked fields. Unit: `policy.test.ts` (5), `HireDetail.test.tsx › who sees a reveal control` (3). Playwright mutations P1 (HR may reveal Aadhaar) and P3 (managers get Hires) fail this spec (`docs/evidence/UI-4/playwright-mutations.txt`). |
| Playwright: a document upload shows its type, upload time and verified flag | yes | `hires.spec.ts › a document upload shows its type, upload time and verified flag`: uploads a PNG as PAN, the table row shows `PAN`, `Not verified`, a `UTC` time whose `datetime` is within the test window, never the file name; the row is still there after navigating away and back (read from `GET /hires/{id}/documents`). Unit: `HireDetail.test.tsx › documents (FR-P3)` (3). API: `test_documents.py` list tests (7), `test_role_matrix.py` row, `tools/tests_xt` route case, `tests_int/test_mongo_documents.py::test_the_list_route_on_mongo_…` (real Mongo). |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|
| `GET /hires/{hire_id}/documents` → `DocumentsEnvelope` (`data: DocumentMeta[]`, oldest first). HR/HR_ADMIN only (manager 403, no service 503); another org's or a missing hire is the same 404. Metadata only. TS client: `listHireDocuments`; `@onb/ts-api-client` now exports `DocumentMeta`, `DocumentType`, `HireRecord`, `NewHireRequest`, `PiiField`, `RevealedValue`. | `test_documents.py` (7 `list` tests), role matrix, `test_xt_api.py ROUTE_CASES[("GET","/hires/{hire_id}/documents")]`, Mongo int test; `make contracts-check` up to date |
| Revealed values live only in `useReveal` state (never the query cache), each dropped after `expires_in_seconds`, on hide, on unmount, and an answer that lands after hide/clear/unmount is discarded. | `useReveal.test.tsx` (8); mutants M3, M4, M14 |
| `canRevealField(role, field)` mirrors the API (HR_ADMIN all, HR all but Aadhaar, MANAGER none); `canManageHires(role)` gates the add form and upload form. | `policy.test.ts`; M1, M2 |
| `/hires/*` is built: list + add form at `/hires`, detail at `/hires/:hireId`. `App.tsx` mounts `HiresPage` at `/hires/*` behind `RequireSection`; `nav.ts` unchanged (UI-1's table still decides access). | `App.test.tsx` (unchanged, passes), `hires.spec.ts` |
| Playwright shared steps in `apps/web/e2e/helpers.ts` (`signInAs`, `openSection`, `goInApp`, `createHire`, synthetic `RAW` values). The e2e API server (via `admin_seed.py`) serves hires and documents, and the seeded hire from UI-8 (Meera Iyer) is in the list too; use `E2E_API_PORT`/`E2E_WEB_PORT` to avoid port clashes between sessions. | `playwright-direct.txt` |

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|
| U3 @ MS-6 | — | contributes evidence only; MS-6 is the last prover | `hires.spec.ts` | no |

## Gates
| Gate | Command | Result |
|------|---------|--------|
| Check | `make check` | exit 0, `make check: all passed`; `Successfully ran target lint/typecheck/test for 17 projects`; test-xt `cross-tenant surface: 25 API routes, 8 MCP tools, 0 event consumers`, `227 passed` (`docs/evidence/UI-4/make-check.txt`) |
| Integration | `make test-int` | exit 0, `Successfully ran target test-int for 8 projects` (api `44 passed`, incl. the new list test) (`docs/evidence/UI-4/make-test-int.txt`) |
| Contracts | `make contracts-check` | `contracts-check: openapi.json and the TS client are up to date` |
| Playwright | `npx playwright test --config apps/web/playwright.config.ts` (ports 19810/19811) | `12 passed` (5 UI-1 + 3 UI-4 + 4 UI-8) |
| Web unit | `vitest run --config apps/web/vitest.config.ts` | `97 passed (97)`, 10 files |
| Eval self-test | not run | no agent, prompt, manifest or guardrail touched |

Environment: compose project `ob-ui-4` (ports 19800/19801), `LLM_CONNECTOR=fake`. Playwright ran direct against the e2e API + Vite, not through Caddy.

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|
| UI-1 | `MaskedField` renders masked by default; reveal control only with `onReveal`; a `ptk_` never renders | yes; every field goes through it |
| UI-1 | `NAV_ITEMS`/`canAccess` guard routes; screens replace the `Guarded` placeholder | yes; managers never reach `/hires` |
| UI-1 | token in memory only; `queryClient.clear()` at session end | yes; hire records cached by TanStack Query are masked-only and cleared on sign-out |
| HIRE-1 | `POST /hires`, `GET /hires`, `GET /hires/{id}` masked, reveal needs reason, Aadhaar HR_ADMIN only, answer `no-store`, `expires_in_seconds` 30 | yes |
| HIRE-2 | upload contract (raw body, media type, `?type=`), Aadhaar scan needs consent | yes; the form sends the Aadhaar consent purpose with the hire |

## Mutation checks
`uv run --frozen python docs/evidence/UI-4/mutate.py --write` → `docs/evidence/UI-4/mutation-checks.txt`, `SUMMARY: 14 of 14 mutations caught`; files restored by `git checkout`. M1 HR reveals Aadhaar → `lets HR reveal every field but Aadhaar`; M2 manager reveals → `never lets a manager reveal anything`; M3/M4 no or wrong TTL → `holds a revealed value, then drops it after the TTL the API names`; M5 no reason needed → `asks for a reason, reveals one field, and hides it again after the TTL`; M6 wrong upload media type; M7 non-scan sent; M8 Aadhaar without consent; M9 unverified shown as verified; M10 list route admits managers → `test_a_manager_cannot_list_scans`; M11 list returns every hire's scans; M12 no hire/org check on the list → `test_listing_the_scans_of_a_missing_or_foreign_hire_is_404`; M13 order; M14 late answer after hide/clear/unmount still shown → `drops an answer that arrives after unmount…`. Playwright-level: P1, P2 (a value never expires → the TTL spec fails), P3, each failing the named spec (`playwright-mutations.txt`).

## Review findings left open
A code review and a security review (general-purpose agents with reviewer briefs, on `git diff main...HEAD`) found no critical or high issue. Fixed: medium late reveal after unmount/hide/clear (ticket guard + 2 tests, M14); medium generated `apps/web/.vitest/json/output.json` had been committed (removed, `.vitest/` ignored); low an in-flight reveal closing another field's reason form.
Left open:
- low · `apps/api/src/onb_api/documents/meta.py:46` · ties on `uploaded_at` (ms precision) fall back to the random `_id`, so two scans uploaded in the same millisecond list in arbitrary order; HTTP uploads are far apart, so the Mongo test is not flaky in practice · acceptable.
- low · `apps/web/e2e/hires.spec.ts:16` · `page.clock.install()` fakes all app timers; fine today, revisit if auth gains timers.
- low · `apps/web/e2e/hires.spec.ts` · the manager is sent to a path by `pushState` + `popstate` (UI-1's pattern; a full load drops the token by design, ADR 0005).
- low · client checks only the media type, not size or magic bytes; the API enforces 413 and signatures (HIRE-2). Reason length/non-blank are also API-enforced (`RevealRequest`).
- Review claim not accepted: "hire stays cached after sign-out" — `AuthProvider` calls `queryClient.clear()` when the session ends (UI-1 tests).

## Left undone, and why
- U3's "pick a template" and "onboarding starts automatically" are not in the card's Done-when; they belong to the run goals (MS-5/MS-6). The add form has no template picker.
- No document download or verify action (no route; PRD has no UI for it in this card).
- Playwright through Caddy was not run (direct only).

## Found for other goals
- There is no users/managers list route, so the form takes the **Manager ID** as text (the API checks it is a manager in the org). A `GET /users?role=MANAGER` would allow a picker.
- `apps/mcp-hub/tests_int/test_hub_xt.py::test_org_a_cannot_reach_org_b_through_hub_tool[mongo__find]` failed once in a `make check` run: its random marker (`09099264404b`) tripped the hub's `raw_pii_in_arguments` guard; it passed on re-run. A marker with letters in every position (or a fixed one) would remove the flake. Not touched here.
- `make test-xt` showed transient Mongo "connection closed / No keys found for HMAC" errors once on a tunnelled port; re-run passed (210). Environmental.
- `App.tsx`: after UI-8 merged, this goal's edit is one import and one `<Route path="/hires/*">` next to UI-8's `/admin/*`.

## Attempt 2: rebase on main 8a26916 (HIRE-2, SEED-1, UI-8 merged)
`git rebase main` (orchestrator-authorised), five commits replayed, no history rewritten beyond that. Conflicts, both sides kept:
- `apps/api/tests/test_role_matrix.py`: UI-8's `/evals/runs` rows and this goal's `GET /hires/{hire_id}/documents` row.
- `apps/web/src/App.tsx`: UI-8's `RequireSection` and `/admin/*` kept; hires mounted the same way (`/hires/*`), `Guarded` unchanged from main. `HiresPage`'s inner routes became relative (`index`, `:hireId`) for the new parent.
- `apps/web/e2e/serve_api.py`: main's version (`create_app(..., **admin_services(users))`); the hire and document services now come from `admin_seed.py`, whose `hires()` returns the `DocumentService` too, and `admin_services` passes `documents`.
HIRE-2 on main has no documents list route, so this goal's `GET /hires/{hire_id}/documents` stays. Gates re-run on the rebased branch (table above); mutations re-run, `14 of 14 caught`; contracts regenerated by the merge are still up to date (`make contracts-check`).

## Decisions forced (ADRs written)
None.

## Traps hit
- `rtk`-wrapped vitest wrote `apps/web/.vitest/json/output.json` into the tree and it was committed; removed and ignored.
- Reveal answers are the typed form (`+91 90000 00042`), not the canonical digits; the Playwright assertion uses the typed value.
- A descendant `<Routes>` under the `path="*"` guard route resolves relative to `/`, so `HiresPage` declares its paths as `/hires` and `/hires/:hireId`.
