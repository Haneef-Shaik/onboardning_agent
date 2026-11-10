---
name: verify-goal
description: Independently verify one Onboarding Platform MVP goal or milestone by checking every Done-when box (or Exit check) against real evidence, re-running the gates, checking ledger rows and the standing rules, and returning a pass/fail verdict as a fixed JSON block. Used by the goal-verifier agent; read-only.
argument-hint: "[goal-id] [branch] [attempt]"
---

# Verify a goal or a milestone

You check work you didn't do. You never edit, commit, check out, reset or stash anything. You run
commands to read and to test, nothing else.

## Inputs

- the goal id, or a milestone id (`MS-n`);
- the attempt number;
- for a goal: the branch. For a milestone you check `main`.

## Procedure

1. **The contract.** `python3 scripts/plan/plan.py show <ID>` prints the boxes. Copy them verbatim:
   they are your checklist. Never invent your own and never drop one.
   - For a milestone, also read each need's status on the board
     (`https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA`, collection `goals`) with ArtifactData
     `get`. Any need not `done` is an immediate fail.
2. **The claim.** For a goal: `git show <branch>:docs/plan/runs/<ID>/handoff.md`. For a milestone:
   each need's handoff on `main`.
3. **The change** (goal only). `git diff main...<branch>` and `git log --oneline main..<branch>`.
   Read changed files in full where a hunk isn't enough (`git show <branch>:<path>`).
4. **Each box.** Find evidence you can check yourself:
   - **A test:** it exists, it asserts what the box names (read it), and you ran it and saw it pass.
     Run tests against the branch's tree without switching the main checkout: use a temporary
     worktree in the scratchpad (`git worktree add <scratch>/verify-<ID> <branch>`), run there, and
     remove the worktree when done.
   - **A guard / mutation check:** the handoff records how it was broken and which test failed.
     Confirm that test really covers the guard. Where cheap, break it yourself in the scratch
     worktree, run, and restore.
   - **A measurement or eval report:** it is where the box says, states method, hardware and sample
     size, and meets the threshold. For evals, check the report's gate numbers yourself.
   - **A file, doc or ADR:** it exists at the path and its content matches the box.
   - **An owner action** (Bedrock access, VM, CI run link): the evidence file records it with a date.
     Check what you can locally and say what you couldn't.

   A box with only the handoff's word for it is **not met**.
5. **Ledger rows.** For each ACCEPTANCE.md row the goal appears in: if it is the row's last prover,
   the row must now read `pass · YYYY-MM-DD · <env> · <evidence>` and the evidence must exist. If it
   isn't the last prover, the diff must not change that row.
6. **Gates.** In the scratch worktree, run the `docs/MASTER-PLAN.md` §7 gates for what the diff
   touches (always `make check`; `make test-int`, `make test-xt`, `make contracts-check`,
   `make evals-selftest` where they exist and apply). A gate you couldn't run is `not-run`, and a
   `not-run` gate the goal needs is a fail.
7. **Standing rules** (`docs/MASTER-PLAN.md` §9). Check the diff for:
   - **Scope:** work that belongs to another goal, with no reason given.
   - **Quality bars:** added `skip`/`xfail`/`.only`, lowered coverage thresholds, weakened or deleted
     assertions, unexplained `noqa` / `type: ignore` / `eslint-disable`.
   - **Secrets and PII:** keys, tokens, `.env` contents, or anything that looks like a real Aadhaar,
     PAN, mobile, email or bank number outside the synthetic fixtures. Run the PII-3 detectors over
     the diff once they exist.
   - **Invariants:** `org_id` taken from a request or tool argument; data access bypassing
     `TenantRepository`; a new route, tool or consumer without a cross-tenant case; a raw sensitive
     value outside the vault; an agent path to the vault; a token resolved outside a sink; a model id
     or credential in code; an LLM-graded eval.
   - **Status files:** the branch must not tick boxes in GOALS.md.
8. **Decide.** **Pass** only if every box is met with checkable evidence, every ledger row is right,
   every gate you ran passes, and no standing rule is broken. Otherwise **fail**.

Clean up: `git worktree remove <scratch>/verify-<ID>`.

## The verdict: your final message, exactly this shape

```json
{
  "goal": "<ID>",
  "attempt": 1,
  "verdict": "pass | fail",
  "boxes": [
    { "box": "<box text, verbatim>", "met": true, "evidence": "<command + result line, or file:line, or path>" }
  ],
  "ledger": [
    { "row": "<ID@gate>", "ok": true, "note": "<what was checked, or what is wrong>" }
  ],
  "gates": { "check": "pass | fail | not-run", "integration": "pass | fail | not-run | n/a", "cross_tenant": "pass | fail | not-run | n/a", "evals": "pass | fail | not-run | n/a" },
  "rules": { "scope": "ok | <problem>", "quality_bars": "ok | <problem>", "secrets_pii": "ok | <problem>", "invariants": "ok | <problem>", "status_files": "ok | <problem>" },
  "problems": ["<each reason for a fail: box, row or rule, what is missing, where>"],
  "retry_guidance": "<for a fail: what the next attempt must do, in order>"
}
```

Then one plain sentence. No other text.
