# Master plan — Agentic HR Onboarding Platform (MVP)

| | |
|---|---|
| Source | [PRD v0.3](PRD.md), 10 November 2026 |
| Goals | [plan/GOALS.md](plan/GOALS.md) (67 cards: 60 goals, 7 milestone gates) |
| Evidence ledger | [plan/ACCEPTANCE.md](plan/ACCEPTANCE.md) |
| Live board | [Goal board](https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA) |
| Team | One developer (Haneef), working through Claude Code sessions |

## 1. What this plan governs

The PRD says **what** to build. This plan says **in what order**, **what counts as finished**, and
**how each piece is handed to the next**. If the PRD and a goal card disagree, the PRD wins and the
card is corrected.

The work is split into **goals**. Each goal is one coherent outcome that one Claude Code session
(or two or three, for size L) can finish and prove. Goals depend on each other; the board works out
what is ready.

## 2. Why goals, not tasks

A task list answers *what's next*. It doesn't answer *what may the next piece of work rely on*. On a
project built across many sessions, that gap is where the damage happens: a test that passed by luck
is handed forward as "proven", an assumption in one step is silently inherited by the next.

So every goal is a **contract** with a checkable definition of done, and ends with a **handoff**: the
specific claims the next goal may rely on, each backed by something that was run, not something
that was believed. **A claim in a handoff must be something you ran.** That is the whole method.

It also matches how Claude Code's [`/goal`](https://code.claude.com/docs/en/goal) works: you give a
session a completion condition with one measurable end state and a stated check, and a separate
evaluator model decides after every turn whether the condition holds. A goal card's **Done when**
list is exactly that condition (see §10).

## 3. The anatomy of a goal card

| Part | Answers | Rule |
|---|---|---|
| **Title** | The outcome, in product terms | A sentence a reviewer can check, not a component name |
| **Size · Milestone · Label** | How big, which release, short name on the board | S = half a day, M = a day, L = two or three sessions |
| **Needs** | Which goals must be Done first | Only real dependencies. Ready/blocked is derived from these |
| **Outcome** | What is true afterwards that isn't true now | One or two sentences |
| **PRD** | Which sections, `FR-*`, `U*` and targets it satisfies | The build check fails if any PRD `FR-*` is on no card |
| **Scope** | What to do, in order | Anything not listed goes to the handoff's "Found for other goals" |
| **Done when** | The contract | Every box independently checkable by someone who wasn't there |
| **Traps** | How it goes wrong | Written before it does |

Milestone cards (`MS-*`) have **Exit checks** instead of Done when.

## 4. Milestones

| Gate | PRD | Declares |
|---|---|---|
| **MS-0** | M0 Foundations | Nx monorepo, Compose stack (Mongo rs + auth, collector, Jaeger, Caddy), CI, telemetry |
| **MS-1** | M1 Core platform | Contracts, JWT + roles, tenant isolation + cross-tenant suite, PII vault with grants, hires + reveal |
| **MS-2** | M2 Mongo MCP | Real Mongo MCP with validator and least-privilege users, MCP hub with registry policy and audit, mocks |
| **MS-3** | M3 LLM + templates + evals | Connector registry, template validator, DAG compiler, eval harness, Template Builder (draft + suggest) |
| **MS-4** | M4 Runtime | Event bus, LangGraph orchestrator, task agents, guardrails, approvals via the Telegram mock, run chat |
| **MS-5** | M5 Frontend | Every screen and user story in the browser |
| **MS-6** | M6 Hardening | PII leak/tamper proof, security review, eval baseline, latency, golden-path E2E, VM deployment, success demos |

Milestones are gates, not phases: a goal from a later milestone may start as soon as its needs are
Done (the web shell UI-1 can start in step 5). The board's columns show the earliest step for each.

## 5. One developer: how the work flows

There are no lanes and no parallel teams. One person runs one goal at a time, each in its own Claude
Code session:

1. Open the board, **Ready to run** tab. Pick the top ready goal (the board sorts by how many goals
   it unblocks). Prefer the critical path: F → T → PII → HUB → AG → RUN → HIL.
2. In a fresh session in this repo, type `/run-goal <ID>`. The skill checks the entry gate, branches,
   reads the card and the PRD sections, then sets a `/goal` condition generated from the card and
   works until it holds.
3. Before the condition can be met, a **separate `goal-verifier` agent** (read-only, fresh context)
   checks every box against real evidence. You don't review your own work with the same eyes that
   wrote it; neither does Claude.
4. You merge the branch to `main` and the goal is set **Done** on the board.

Owner actions (`OW-1` Bedrock access, `OW-2` VM and domain) are goals like any other, but only you
can do them. They sit on the board so they don't surprise a later goal.

## 6. Branches and integration

- `main` is the integration branch. F-1 initialises the repository (it isn't one yet).
- Every goal works on `goal/<id-lowercase>-<label-slug>`, branched from an up-to-date `main`.
  `python3 scripts/plan/plan.py show <ID>` prints the branch name.
- Merge with a fast-forward after the verifier passes: `git rebase main`, re-run the standard gates,
  `git merge --ff-only`. No merge commits, nothing forced.
- Nothing is pushed or deployed by Claude unless you say so for that goal.

## 7. Evidence and gates

**The standard gates.** Run from the repo root on the branch being checked. Each becomes available
when the goal that creates it is Done.

| Gate | Command | Required when the diff touches | Exists after |
|---|---|---|---|
| Check | `make check` (lint, mypy/tsc, unit tests, coverage ≥ 80% on `libs/py-*`, plan check) | always | F-1 |
| Integration | `make test-int` (pytest + testcontainers Mongo, compose up) | `apps/*`, `libs/py-data`, `py-crypto`, `py-eventbus`, `infra/` | F-2 |
| Cross-tenant | `make test-xt` | any route, MCP tool or event consumer | T-3 |
| Contracts | `make contracts-check` | `libs/py-contracts`, API models | C-1 |
| Eval self-test | `make evals-selftest` (fake connector, no model calls) | `libs/py-agents`, `py-evals`, `py-guardrails`, prompts, manifests | EVAL-1 |
| Eval full | `nx run evals:run --agent <name> --llm standard --repeat 3` | prompts, manifests, tools, guardrails, model aliases | EVAL-1 + OW-1 |
| PII scan | `make test-pii` | anything that writes, logs, traces or prompts | SEC-1 |
| E2E | `make e2e` (Playwright golden path) | `apps/web`, any API used by the golden path | E2E-1 |

**Meets the eval gates** means: safety cases 100% pass^3; capability ≥ 90% pass@1 and ≥ 80% pass^3;
no drop of more than 5 points against the last baseline (PRD §8.2).

**Where evidence goes.**

| Evidence | Location |
|---|---|
| Each Done-when box with its proof, gates run, mutation checks, open findings | `docs/plan/runs/<ID>/handoff.md` |
| Progress between sessions of one goal | `docs/plan/runs/<ID>/checkpoint.md` |
| Measurements, eval reports, walkthroughs, review reports | `docs/evidence/<ID>/` (a measurement states method, hardware, sample size) |
| Acceptance results | [plan/ACCEPTANCE.md](plan/ACCEPTANCE.md), written only by the row's last prover |
| Decisions a goal forced | `docs/adr/NNNN-<slug>.md` |

A box is **met** only when a verifier can check it: a test they ran and saw pass, a command's output,
a file at the stated path whose content matches, or a measurement recorded where the box says. The
handoff's word alone is not evidence. Evidence uses the synthetic fixtures only, never real people's
data.

## 8. Declaring done

| Level | Declared when | Recorded |
|---|---|---|
| **Goal** | Every Done-when box has checkable evidence, `goal-verifier` passed, gates pass after rebasing on `main`, and it is merged | Board status Done with the merge commit as ref |
| **Milestone** | Every need is Done on the board and `goal-verifier` (milestone mode) passes every Exit check | Board status Done; evidence in `docs/evidence/MS-n/` |
| **MVP** | MS-6 is Done | Board; a line in the PRD changelog |

- Only **Done** resolves a need. **In review** means verified but not yet merged; dependents stay
  blocked until it's on `main`.
- Never set a goal Done while one of its needs isn't Done.
- The boxes in GOALS.md stay unticked. They are the contract; the evidence lives in handoffs and the
  ledger.

## 9. Standing rules

1. **The PRD wins.** Read the sections on the card's PRD line before changing anything. If the card
   contradicts the PRD, report it in the handoff; don't build the contradiction.
2. **Test first.** Write the test, see it fail, then make it pass. Mutation-check every guard: break
   it on purpose, see a named test fail, and record the output.
3. **Quality only goes up.** Never lower a coverage threshold, skip or weaken a test, or add an
   unexplained `noqa` / `type: ignore` / `eslint-disable`.
4. **Stay in scope.** One goal per branch. Work that belongs to another goal goes in the handoff
   under "Found for other goals".
5. **Product invariants.** These hold in every goal:
   - `org_id` comes only from the signed JWT or agent token, never from a request or tool argument (§7.9).
   - All database access goes through `TenantRepository`; foreign ids return 404, not 403 (§7.9).
   - Every new route, MCP tool and event consumer gets a cross-tenant case (§7.9 Testing).
   - Raw sensitive values exist only in the vault. Everything else carries `ptk_` tokens (§5.5, §7.10).
   - Only humans (HR/HR_ADMIN via the API) write the vault; Aadhaar is revealed by HR_ADMIN only and never resolved by an agent sink (§7.10).
   - Tokens resolve only at a sink, with an orchestrator-signed grant; never inside a prompt (§7.10).
   - Agents reach tools only through the MCP hub, with the manifest enforced server-side (§5.2, §7.4).
   - The LLM never adds, removes or reorders tasks in a running DAG (§5.1).
   - No credentials or model ids in code; connectors and models are configuration (§7.1).
   - Every LLM and tool call produces a span; spans and logs are PII-scrubbed (§5.7, §7.7).
   - Evals are graded by code only, never by an LLM judge (§8.2).
6. **Nothing outward-facing without you.** No push, deploy, real message or paid model call unless
   the card names it (OW-1, TB-1 and the eval goals use Bedrock; DEP-1 deploys).
7. **Verify, don't assert.** Measurements beat claims.
8. **Commits** use the conventional format with the goal id: `feat(T-1): tenant repository`,
   `test(PII-2): tamper cases fail closed`.
9. **Status and results.** Status changes only on the board. A ledger result is written only by that
   row's last prover, on its goal branch.

## 10. Running a goal with `/goal`

Claude Code's `/goal <condition>` keeps a session working, turn after turn, until a separate
evaluator model judges the condition met (or impossible). The official guidance
([docs](https://code.claude.com/docs/en/goal)) shapes how this plan writes conditions:

- **The evaluator only reads the conversation.** It runs no commands and reads no files. So every
  box must be demonstrable by output Claude prints in the session: a test result line, a command's
  exit code, a file's content. That is why boxes name commands and paths.
- **One measurable end state, a stated check, and the constraints that matter.** The generated
  condition lists every box verbatim, names the gate commands, and states the constraints (stay in
  scope, never weaken tests, no raw PII or secrets, nothing outward-facing).
- **Bound it.** Each condition ends with a turn budget by size (S 30, M 60, L 100 turns) and tells
  Claude to write a checkpoint and stop if the budget runs out.
- **≤ 4,000 characters.** `plan.py goal` checks this; `test_plan.py` fails if any card's condition
  is too long (split the card instead).
- **Run it in auto mode** so goal turns don't stop for every tool permission. `/goal` alone doesn't
  change the permission mode.
- `/goal` with no argument shows status; `/goal clear` stops it; a resumed session restores an
  active goal.

Generate a condition by hand with `python3 scripts/plan/plan.py goal <ID>`. Normally `/run-goal <ID>`
does this for you after the entry gate.

## 11. Planning tools

```bash
python3 scripts/plan/plan.py check          # validate GOALS.md, ACCEPTANCE.md and PRD coverage
python3 scripts/plan/plan.py list           # every goal with its needs
python3 scripts/plan/plan.py show HIRE-1    # card, branch, needs, unblocks, ledger rows
python3 scripts/plan/plan.py goal HIRE-1    # the /goal condition for that goal
python3 scripts/plan/plan.py build --board  # regenerate plan.in.json and board.html after editing cards
python3 -m unittest scripts/plan/test_plan.py
```

**Changing the plan.** Edit the cards in GOALS.md (and ledger rows in ACCEPTANCE.md if a goal starts
or stops proving one), run `plan.py build --board`, then republish the board to the same URL (ask
Claude: "republish the goal board"). Statuses survive, because they are stored by goal id in the
board's database.

**Updating status.** On the board: open a goal, set the status, add the branch or commit, Save. Or ask
Claude in any session: "set T-1 to done with ref 1a2b3c4 on the goal board". The board doesn't write
back to this repo, and nothing in this repo reads it, so the board is the single place status lives.
