# Onboarding Platform (agentic HR onboarding MVP)

- **Requirements:** [docs/PRD.md](docs/PRD.md) (v0.3). The PRD wins any conflict.
- **Plan and method:** [docs/MASTER-PLAN.md](docs/MASTER-PLAN.md). Goals with their contracts are in
  [docs/plan/GOALS.md](docs/plan/GOALS.md); acceptance evidence in
  [docs/plan/ACCEPTANCE.md](docs/plan/ACCEPTANCE.md).
- **Live status:** the goal board, https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA (collection
  `goals`, one document per goal id). It is the only place status lives.
- **One developer** (Haneef). One goal per session, run with `/run-goal <ID>`; it sets a `/goal`
  condition from `python3 scripts/plan/plan.py goal <ID>` and finishes with an independent
  `goal-verifier` pass.
- **Standing rules** are in MASTER-PLAN §9. The ones most often at risk: `org_id` only from the
  signed token; all DB access through `TenantRepository`; raw PII only in the vault (tokens
  everywhere else); agents reach tools only through the MCP hub; evals graded by code only; no
  credentials or model ids in code; never weaken a test to get green.
- After editing GOALS.md or ACCEPTANCE.md: `python3 scripts/plan/plan.py build --board`, then
  republish `docs/plan/board.html` to the board URL.
