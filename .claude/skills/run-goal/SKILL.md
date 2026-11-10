---
name: run-goal
description: Run one Onboarding Platform MVP goal end to end — entry gate on the goal board, branch, read the card and PRD, set a /goal condition generated from the card, build test-first with checkpoints, prove every Done-when box, get an independent goal-verifier pass, merge locally and mark it Done on the board. Use when the user types /run-goal <ID> or asks to work on a goal from docs/plan/GOALS.md.
argument-hint: "[goal-id]"
---

# Run one plan goal

The goal id comes with the skill (`/run-goal T-1`). One goal per session. The project has one
developer, Haneef; you work for him on his machine.

Board URL: `https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA`. Statuses live in its database,
collection `goals`, one document per goal id: `{status, who, ref, note, by, at}`. Status is one of
`not started`, `claimed`, `in review`, `done`, `on hold`. Load the `ArtifactData` tool with ToolSearch
if it is deferred.

## 1. Entry gate

```bash
python3 scripts/plan/plan.py show <ID>
```

It prints the card, the branch name, the needs, what the goal unblocks and its ledger rows. For each
need, `ArtifactData get` collection `goals`, doc `<need>` (a missing document means `not started`).
Every need must be `done`. If one isn't, **stop** and tell Haneef which need is open and what it
waits on. Don't start a blocked goal unless he explicitly says to.

## 2. Claim and branch

- On the board, write `goals/<ID>` = `{status: "claimed", who: "Haneef", ref: "<branch>", note: "", by: null, at: <now ISO UTC>}`
  (pass `if_version` if the document exists), and add an `activity` document
  `{kind: "auto", goal, from, to: "claimed", who: "Haneef", ref, note: "started by /run-goal", by: null, at}`.
- `git switch main && git pull --ff-only` if a remote exists, then `git switch -c <branch>`.
  (Before F-1 is Done there is no repository; F-1 itself runs `git init`.)
- **Resume, don't restart.** If `docs/plan/runs/<ID>/checkpoint.md` exists on the branch, read it
  first and continue from where it says.

## 3. Read before touching anything

1. The whole card in `docs/plan/GOALS.md`: outcome, PRD line, scope, Done when, traps.
2. The PRD sections on the card's PRD line, in `docs/PRD.md`. The PRD wins any conflict; report a
   conflict in the handoff rather than building it.
3. `docs/MASTER-PLAN.md` §7 (evidence and gates) and §9 (standing rules).
4. The handoff of every need: `docs/plan/runs/<need>/handoff.md`. Verify what it claims before
   relying on it.
5. For agent goals: `evals/README.md` and the cases for that agent in
   `evals/datasets/onboarding_agents_v1.jsonl`.

## 4. Set the /goal condition

```bash
python3 scripts/plan/plan.py goal <ID>
```

Enter the printed text as the `/goal` condition (it starts with `/goal`). It lists every Done-when
box, the gate commands, the verifier pass, the constraints and a turn budget. The evaluator only reads
the conversation, so prove each box by printing the command and its output in this session.

If you can't set a `/goal` yourself in this environment, tell Haneef to paste it, then carry on
working toward the same condition.

## 5. Build test-first, in steps

Work through the card's scope in order. For each step:
1. Write the test, run it, see it fail.
2. Make it pass with the smallest change.
3. Refactor. Run the step's tests again.
4. Commit (`feat(<ID>): …`, `test(<ID>): …`), then update `docs/plan/runs/<ID>/checkpoint.md`
   (steps done, next step, surprises) and commit it.

Mutation-check every guard the card names: break it on purpose, run, record which named test failed,
then restore it. Never rewrite history on the branch; undo with a revert commit.

## 6. Prove each box

- Put evidence where `docs/MASTER-PLAN.md` §7 says: a test you ran (name, command, result line), a
  command's output, a file in `docs/evidence/<ID>/`, an eval report.
- **Acceptance ledger.** `plan.py show` lists the ledger rows the goal appears in. Only where this
  goal is the **last** prover, set that row's result in `docs/plan/ACCEPTANCE.md` to
  `pass · YYYY-MM-DD · <compose | VM> · <evidence path>`. Otherwise leave the row alone.
- Run `python3 scripts/plan/plan.py check` after touching GOALS.md or ACCEPTANCE.md.

## 7. Review your own diff

Run `code-reviewer` on `git diff main...HEAD`, and `security-reviewer` when the diff touches auth,
tenancy, PII, the vault, grants, the MCP hub, guardrails or any route. Fix every critical and high
finding; list the rest in the handoff.

## 8. Run the gates the diff touches

From `docs/MASTER-PLAN.md` §7: always `make check`; `make test-int`, `make test-xt`,
`make contracts-check`, `make evals-selftest` and the real eval run when the diff touches what they
cover and the goal that creates them is Done. Keep the commands and result lines for the handoff.

## 9. Write the handoff

`docs/plan/runs/<ID>/handoff.md`, committed. The verifier reads only this, the card and the diff:

```markdown
# Handoff — <ID> · <title>        attempt <n> · <branch> · <HEAD>

**Outcome claimed.** <one sentence>

## Done when
| Box | Met | Evidence |
|-----|-----|----------|
| <box text, verbatim> | yes/no | <test name + command + result line, file path, or report> |

## Claims the next goals may rely on
| Claim | Proved by |
|-------|-----------|

## Acceptance evidence
| Ledger row | Environment, date | Result | Evidence | Row written here? |
|------------|-------------------|--------|----------|-------------------|

## Gates
| Gate | Command | Result |
|------|---------|--------|

## Inherited and used
| From | Claim | Held? |
|------|-------|-------|

## Mutation checks
<guard → how it was broken → which test failed (output line)>

## Review findings left open
<severity · file:line · finding · why it is left>

## Left undone, and why
## Found for other goals
## Decisions forced (ADRs written)
## Traps hit
```

## 10. Independent verification

Start the `goal-verifier` agent with: the goal id, the attempt number, and the branch. It is
read-only and returns a JSON verdict. On `fail`, do what its `retry_guidance` says, update the
handoff, and run it again (new attempt number). After three failed attempts, stop and tell Haneef
what keeps failing and what you recommend (retry differently, split the card, or change it).

## 11. Close

1. Set `goals/<ID>` to `in review` on the board (ref: branch @ HEAD) and add an `activity` entry.
2. Unless Haneef said to hold for his review: `git rebase main`, re-run the gates on the rebased
   branch, `git switch main && git merge --ff-only <branch>`. Never push, never force, never create a
   merge commit. If the rebase conflicts, `git rebase --abort` and report the files.
3. Set `goals/<ID>` to `done` with `ref: <short merge commit>` and add an `activity` entry
   (`from: "in review", to: "done"`).
4. `git branch -d <branch>` (never `-D`).
5. Report: what was built, the evidence for each box, which goals are now ready (goals whose needs are
   all `done`), and what the next session must know.
