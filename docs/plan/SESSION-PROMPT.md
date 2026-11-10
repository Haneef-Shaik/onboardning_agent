# Session prompt: run one goal

> Start a fresh Claude Code session in `/Users/Adya/personals/onboardning_agent`, switch to auto
> mode, then type **`/run-goal <ID>`**, for example `/run-goal T-1`. One goal per session.
>
> The `run-goal` skill (`.claude/skills/run-goal/SKILL.md`) is the procedure. If skills aren't
> available, paste everything below the line, replacing `<ID>`.

---

You are running goal **`<ID>`** of the Onboarding Platform MVP plan. Follow
`.claude/skills/run-goal/SKILL.md`. In short:

1. **Entry gate.** Run `python3 scripts/plan/plan.py show <ID>`. On the goal board
   (https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA), read `goals/<need>` for every need with the
   ArtifactData tool. Every need must be `done`; if one isn't, stop and tell me which.
2. **Claim.** Set `goals/<ID>` to `claimed` (who: Haneef, ref: the branch) on the board, and create
   the branch `plan.py show` prints, from an up-to-date `main`.
3. **Read.** The whole card in `docs/plan/GOALS.md`, the PRD sections on its PRD line,
   `docs/MASTER-PLAN.md` §7 and §9, the handoffs of the goals it needs (`docs/plan/runs/<need>/handoff.md`),
   and any checkpoint for this goal.
4. **Set the goal.** Run `python3 scripts/plan/plan.py goal <ID>` and enter its output as the
   `/goal` condition.
5. **Build test-first** in the card's scope order. Commit and update
   `docs/plan/runs/<ID>/checkpoint.md` after each step.
6. **Prove every box** with output printed in this session. Write an ACCEPTANCE.md result only for
   rows where this goal is the last prover.
7. **Review** the diff with `code-reviewer` and `security-reviewer`; fix critical and high findings.
8. **Handoff.** Write `docs/plan/runs/<ID>/handoff.md` in the skill's format.
9. **Verify.** Run the `goal-verifier` agent on the branch. Fix and re-verify until it passes.
10. **Close.** Set the goal to `in review` on the board. Rebase on `main`, re-run the gates, and
    fast-forward `main` locally (never push). Then set it to `done` with the merge commit as ref, and
    tell me which goals that unblocks. If I said "hold for my review", stop at `in review` instead.

A goal is **Done** only when every box has checkable evidence, the verifier passed, and it is merged
to `main`.
