# Goal board

The plan for the Onboarding Platform MVP: 67 goals from [PRD v0.3](../PRD.md), their dependencies,
their contracts, and where live status is kept. The method is in [MASTER-PLAN.md](../MASTER-PLAN.md).

**Live board:** https://claude.ai/artifact/G52gPhZ6uD63YPdE1CCpCA (private to you; share from its
Share menu if anyone else should see it).

- **Graph:** one lane (you), one column per step. A goal's column is the longest chain of needs
  before it. Hover or select a goal to light up what it needs and what waits on it. The badges
  **F**, **P** and **E** stand for the foundation (F-1, F-4), the platform core (T-1, AUTH-1, AUD-1)
  and the eval harness (EVAL-1), which many goals need; their lines appear on hover.
- **Ready to run:** what you can start now, sorted by how much each one unblocks.
- **All goals** and **Updates:** a filterable table and a feed of every status change.
- **Export CSV** downloads the table.

## Run a goal

```text
/run-goal T-1
```

in a fresh Claude Code session in this repo (auto mode recommended). See
[SESSION-PROMPT.md](SESSION-PROMPT.md) for what happens and a paste-in fallback.

## Update status

Statuses are `Not started`, `Running`, `In review`, `Done` and `On hold`. **Ready** and **Blocked**
are worked out from the needs and never set by hand. Only **Done** resolves a need, so set Done only
once the branch is merged to `main`.

- On the board: open the goal, pick the status, add the branch or commit, Save.
- From a session: "set T-1 to done with ref 1a2b3c4 on the goal board". Claude writes it with the
  ArtifactData tool.

## Change the plan

1. Edit the cards in [GOALS.md](GOALS.md). Its fenced `json` block holds the board settings. Every
   card needs **Done when** (a milestone needs **Exit checks**) and a **PRD** line. If a goal now
   proves a user story, target or metric, add it to that row in [ACCEPTANCE.md](ACCEPTANCE.md).
2. Run `python3 scripts/plan/plan.py build --board`. It refuses unknown needs, cycles, cards without
   a contract, any PRD `FR-*` no card names, and ledger rows whose gate doesn't wait on their goals.
3. Ask Claude to republish `docs/plan/board.html` to the board URL above. Statuses survive.

## Files

| File | Written by | What |
|---|---|---|
| `GOALS.md` | you | Every goal with its contract, plus board settings. **The source of the plan** |
| `ACCEPTANCE.md` | you (results only by each row's last prover) | User stories, NFRs, success metrics, eval gates and decisions, with their goals and gate |
| `SESSION-PROMPT.md` | you | How to run one goal in a fresh session |
| `plan.in.json` | `plan.py build` | GOALS.md as board data. Don't edit |
| `board.html` | `plan.py build --board` | The published page. Don't edit |
| `preview.html` | `plan.py build --board` | Local preview with a stand-in runtime. Never publish; keep out of git |
| `statuses.json` | you | Import file for the board's first seed (empty: nothing built yet) |
| `runs/<ID>/` | goal sessions | Each goal's `checkpoint.md` and `handoff.md` |

Tooling lives in [`scripts/plan/`](../../scripts/plan/): `plan.py` and its tests
(`python3 -m unittest scripts/plan/test_plan.py`). The board page is built by the `dependency-board`
skill at `~/.claude/skills/dependency-board` (override with `DEPENDENCY_BOARD_SKILL`).
