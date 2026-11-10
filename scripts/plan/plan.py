#!/usr/bin/env python3
"""Goal-plan tooling for docs/plan/GOALS.md (standard library only).

Commands:
  check              validate GOALS.md and ACCEPTANCE.md against each other and the PRD
  build [--board]    check, then write docs/plan/plan.in.json (and board.html + preview.html)
  show <ID>          a goal's card, needs, what it unblocks, and its ledger rows
  goal <ID>          the /goal condition for a goal (paste into Claude Code)
  list               every goal, in plan order

Statuses are not kept here: they live on the claude.ai goal board (see docs/plan/README.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = ROOT / "docs" / "plan"
GOALS_MD = PLAN_DIR / "GOALS.md"
ACCEPTANCE_MD = PLAN_DIR / "ACCEPTANCE.md"
PRD_MD = ROOT / "docs" / "PRD.md"

ID_RE = re.compile(r"^[A-Za-z0-9_\-.~:@+]+$")
HEADER_RE = re.compile(r"^### (\S+) · (.+?)\s*$")
META_RE = re.compile(r"\*\*Size:\*\*\s*(\w+)\s*·\s*\*\*Milestone:\*\*\s*(\w+)\s*·\s*\*\*Label:\*\*\s*(.+?)\s*$")
NEEDS_RE = re.compile(r"\*\*Needs:\*\*\s*(.+?)\s*$")
BOX_RE = re.compile(r"^- \[[ xX]\] (.+?)\s*$")
FR_RE = re.compile(r"\bFR-[A-Z]+\d+\b")
RESULT_RE = re.compile(
    r"^(open|(pass|fail) · \d{4}-\d{2}-\d{2} · [^·]+ · .+|n/a · .+)$"
)
SIZES = {"S", "M", "L"}
TURN_BUDGET = {"S": 30, "M": 60, "L": 100}
GOAL_CONDITION_LIMIT = 4000
NO_NEEDS = {"—", "-", "–", ""}


class PlanError(Exception):
    """The plan is invalid; the message lists every problem."""


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    size: str
    milestone: str
    label: str
    needs: tuple[str, ...]
    prd: str
    outcome: str
    boxes: tuple[str, ...]
    box_kind: str  # "Done when" or "Exit checks"
    anchor: str
    order: int


@dataclass(frozen=True)
class LedgerRow:
    id: str
    requirement: str
    proved_by: tuple[str, ...]
    gate: str
    result: str


@dataclass
class Plan:
    config: dict
    goals: dict[str, Goal] = field(default_factory=dict)
    ledger: list[LedgerRow] = field(default_factory=list)


# ------------------------------------------------------------------ parsing

def github_anchor(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def parse_config(text: str) -> dict:
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    if not match:
        raise PlanError("GOALS.md has no ```json config block")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise PlanError(f"GOALS.md config block is not valid JSON: {exc}") from exc


def _split_cards(text: str) -> list[list[str]]:
    cards: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = None
        if HEADER_RE.match(line):
            current = [line]
            cards.append(current)
        elif current is not None:
            current.append(line)
    return cards


def _paragraph_after(lines: list[str], marker: str) -> str:
    for i, line in enumerate(lines):
        if line.startswith(marker):
            parts = [line[len(marker):].strip()]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.startswith("**") or nxt.startswith("- "):
                    break
                parts.append(nxt.strip())
            return " ".join(p for p in parts if p)
    return ""


def _parse_card(lines: list[str], order: int, problems: list[str]) -> Goal | None:
    header = HEADER_RE.match(lines[0])
    assert header
    gid, title = header.group(1), header.group(2)
    meta = needs = None
    box_kind = ""
    boxes: list[str] = []
    in_boxes = False
    for line in lines[1:]:
        meta = meta or META_RE.search(line)
        needs = needs or NEEDS_RE.search(line)
        if line.startswith("**Done when.**") or line.startswith("**Exit checks.**"):
            box_kind = "Done when" if "Done when" in line else "Exit checks"
            in_boxes = True
            continue
        if in_boxes:
            box = BOX_RE.match(line)
            if box:
                boxes.append(box.group(1))
            elif line.startswith("**"):
                in_boxes = False
    if not meta:
        problems.append(f"{gid}: missing the '**Size:** · **Milestone:** · **Label:**' line")
        return None
    if not needs:
        problems.append(f"{gid}: missing the '**Needs:**' line")
        return None
    raw_needs = needs.group(1).strip()
    need_ids = () if raw_needs in NO_NEEDS else tuple(n.strip() for n in raw_needs.split(",") if n.strip())
    return Goal(
        id=gid,
        title=title,
        size=meta.group(1),
        milestone=meta.group(2),
        label=meta.group(3),
        needs=need_ids,
        prd=_paragraph_after(lines, "**PRD.**"),
        outcome=_paragraph_after(lines, "**Outcome.**"),
        boxes=tuple(boxes),
        box_kind=box_kind,
        anchor=github_anchor(lines[0][4:]),
        order=order,
    )


def parse_goals(text: str) -> tuple[dict, dict[str, Goal], list[str]]:
    config = parse_config(text)
    problems: list[str] = []
    goals: dict[str, Goal] = {}
    for order, card in enumerate(_split_cards(text)):
        goal = _parse_card(card, order, problems)
        if goal is None:
            continue
        if goal.id in goals:
            problems.append(f"{goal.id}: duplicate goal id")
            continue
        goals[goal.id] = goal
    return config, goals, problems


def parse_ledger(text: str) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith("| ID ") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        rid, requirement, proved, gate, result = cells
        proved_by = () if proved in NO_NEEDS else tuple(p.strip() for p in proved.split(",") if p.strip())
        rows.append(LedgerRow(rid, requirement, proved_by, gate, result))
    return rows


def load_plan(goals_text: str | None = None, ledger_text: str | None = None) -> tuple[Plan, list[str]]:
    goals_text = goals_text if goals_text is not None else GOALS_MD.read_text(encoding="utf-8")
    ledger_text = ledger_text if ledger_text is not None else ACCEPTANCE_MD.read_text(encoding="utf-8")
    config, goals, problems = parse_goals(goals_text)
    plan = Plan(config=config, goals=goals, ledger=parse_ledger(ledger_text))
    return plan, problems


# ------------------------------------------------------------------ checks

def ancestors(goals: dict[str, Goal], gid: str) -> set[str]:
    seen: set[str] = set()
    stack = list(goals[gid].needs) if gid in goals else []
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in goals:
            continue
        seen.add(cur)
        stack.extend(goals[cur].needs)
    return seen


def find_cycle(goals: dict[str, Goal]) -> list[str] | None:
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(gid: str) -> list[str] | None:
        state[gid] = 1
        path.append(gid)
        for need in goals[gid].needs:
            if need not in goals:
                continue
            if state.get(need) == 1:
                return path[path.index(need):] + [need]
            if need not in state:
                found = visit(need)
                if found:
                    return found
        path.pop()
        state[gid] = 2
        return None

    for gid in goals:
        if gid not in state:
            found = visit(gid)
            if found:
                return found
    return None


def check_goals(plan: Plan) -> list[str]:
    problems: list[str] = []
    tracks = plan.config.get("tracks", {})
    for g in plan.goals.values():
        if not ID_RE.match(g.id):
            problems.append(f"{g.id}: id may only use letters, digits and _ - . ~ : @ +")
        if g.size not in SIZES:
            problems.append(f"{g.id}: size '{g.size}' is not one of S, M, L")
        if tracks and g.milestone not in tracks:
            problems.append(f"{g.id}: milestone '{g.milestone}' is not in the config's tracks")
        if not g.boxes:
            problems.append(f"{g.id}: has no contract (no '- [ ]' boxes under Done when / Exit checks)")
        if g.id.startswith("MS-") and g.box_kind != "Exit checks":
            problems.append(f"{g.id}: a milestone card needs **Exit checks.**")
        if not g.id.startswith("MS-") and g.box_kind != "Done when":
            problems.append(f"{g.id}: a goal card needs **Done when.**")
        if not g.prd:
            problems.append(f"{g.id}: missing the **PRD.** line")
        for need in g.needs:
            if need not in plan.goals:
                problems.append(f"{g.id}: needs '{need}', which is not a goal")
            if need == g.id:
                problems.append(f"{g.id}: needs itself")
    cycle = find_cycle(plan.goals)
    if cycle:
        problems.append("cycle: " + " → ".join(cycle))
    return problems


def check_prd_coverage(plan: Plan, prd_text: str) -> list[str]:
    required = sorted(set(FR_RE.findall(prd_text)))
    named = set()
    for g in plan.goals.values():
        named.update(FR_RE.findall(g.prd))
    return [f"PRD {fr} is not named on any card's **PRD.** line" for fr in required if fr not in named]


def check_ledger(plan: Plan) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in plan.ledger:
        if row.id in seen:
            problems.append(f"ledger {row.id}: duplicate row")
        seen.add(row.id)
        if not RESULT_RE.match(row.result):
            problems.append(f"ledger {row.id}: malformed result '{row.result}'")
        if row.gate == "PROD":
            continue
        if row.gate not in plan.goals or not row.gate.startswith("MS-"):
            problems.append(f"ledger {row.id}: gate '{row.gate}' is not a milestone goal or PROD")
            continue
        gate_needs = ancestors(plan.goals, row.gate)
        for prover in row.proved_by:
            if prover not in plan.goals:
                problems.append(f"ledger {row.id}: proved by '{prover}', which is not a goal")
            elif prover not in gate_needs:
                problems.append(f"ledger {row.id}: gate {row.gate} does not wait on {prover}")
        if not row.proved_by:
            problems.append(f"ledger {row.id}: gated at {row.gate} but proved by no goal")
    return problems


def run_checks(plan: Plan, parse_problems: list[str], prd_text: str | None = None) -> list[str]:
    prd_text = prd_text if prd_text is not None else PRD_MD.read_text(encoding="utf-8")
    return [*parse_problems, *check_goals(plan), *check_prd_coverage(plan, prd_text), *check_ledger(plan)]


# ------------------------------------------------------------------ outputs

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].rstrip("-")


def branch_for(goal: Goal) -> str:
    return f"goal/{goal.id.lower()}-{slug(goal.label)}"


def to_board_plan(plan: Plan) -> dict:
    cfg = plan.config
    owner = cfg.get("owner") or cfg["people"][0]["name"]
    goals = [
        {
            "id": g.id,
            "title": g.title,
            "owner": owner,
            "needs": list(g.needs),
            "label": g.label,
            "size": g.size,
            "track": g.milestone,
            "doc": f"GOALS.md#{g.anchor}",
        }
        for g in sorted(plan.goals.values(), key=lambda x: x.order)
    ]
    return {
        "title": cfg.get("title", "Goal board"),
        "people": cfg.get("people", [owner]),
        "tracks": cfg.get("tracks", {}),
        "syncPoints": cfg.get("syncPoints", []),
        "config": cfg.get("config", {}),
        "goals": goals,
    }


def ledger_rows_for(plan: Plan, gid: str) -> list[tuple[LedgerRow, bool]]:
    return [(r, bool(r.proved_by) and r.proved_by[-1] == gid) for r in plan.ledger if gid in r.proved_by]


def goal_condition(plan: Plan, gid: str) -> str:
    g = plan.goals[gid]
    milestone = g.id.startswith("MS-")
    boxes = " ".join(f"[{i}] {b}" for i, b in enumerate(g.boxes, 1))
    ledger = [r.id for r, last in ledger_rows_for(plan, gid) if last]
    ledger_clause = (
        f" (5) ACCEPTANCE.md rows {', '.join(ledger)} are set to a pass result with evidence, since this goal is their last prover;"
        if ledger else ""
    )
    where = "on main" if milestone else f"on branch {branch_for(g)}"
    verifier = "goal-verifier agent (milestone mode)" if milestone else "goal-verifier agent"
    return (
        f'/goal {"Milestone" if milestone else "Goal"} {g.id} "{g.title}" (docs/plan/GOALS.md) is complete {where}: '
        f"(1) every {g.box_kind} box listed below is shown met in this session by a command run here and its output, "
        f"or by a file at the stated path whose content was shown; "
        f"(2) docs/plan/runs/{g.id}/handoff.md exists with one evidence row per box; "
        f"(3) `make check` exits 0 and its result line is shown (for F-1, the check it creates); "
        f'(4) the {verifier} returned "verdict": "pass" for this work;{ledger_clause} '
        f"Constraints: stay inside this goal's scope (anything else goes under \"Found for other goals\" in the handoff); "
        f"never skip a test, lower a threshold or weaken an assertion; no raw PII, secrets or real credentials in code, "
        f"fixtures, logs or evidence; nothing pushed, deployed or sent off this machine unless the card says so. "
        f"Boxes: {boxes} "
        f"If this is not met after {TURN_BUDGET.get(g.size, 60)} turns, stop, write docs/plan/runs/{g.id}/checkpoint.md "
        f"with the next step, and report."
    )


def render_show(plan: Plan, gid: str) -> str:
    g = plan.goals[gid]
    unblocks = sorted((x.id for x in plan.goals.values() if gid in x.needs), key=lambda i: plan.goals[i].order)
    lines = [
        f"{g.id} · {g.title}",
        f"  size {g.size} · milestone {g.milestone} · label {g.label}",
        f"  card: docs/plan/GOALS.md#{g.anchor}",
        f"  branch: {branch_for(g)}",
        f"  needs: {', '.join(g.needs) or '—'}",
        f"  unblocks: {', '.join(unblocks) or '—'}",
        f"  PRD: {g.prd}",
        f"  Outcome: {g.outcome}",
        f"  {g.box_kind}:",
        *[f"    [{i}] {b}" for i, b in enumerate(g.boxes, 1)],
    ]
    rows = ledger_rows_for(plan, gid)
    if rows:
        lines.append("  ledger rows:")
        lines.extend(
            f"    {r.id} @ {r.gate} — {'LAST prover: writes the result' if last else 'contributes evidence only'} (now: {r.result})"
            for r, last in rows
        )
    return "\n".join(lines)


def build_board(plan_path: Path) -> None:
    skill = Path(os.environ.get("DEPENDENCY_BOARD_SKILL", Path.home() / ".claude/skills/dependency-board"))
    builder = skill / "scripts" / "build_board.py"
    if not builder.exists():
        raise PlanError(f"board builder not found at {builder}; set DEPENDENCY_BOARD_SKILL")
    cmd = [sys.executable, str(builder), str(plan_path), "-o", str(PLAN_DIR / "board.html")]
    statuses = PLAN_DIR / "statuses.json"
    if statuses.exists():
        cmd += ["--preview", str(statuses)]
    subprocess.run(cmd, check=True)


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    b = sub.add_parser("build")
    b.add_argument("--board", action="store_true", help="also rebuild board.html with the dependency-board skill")
    for name in ("show", "goal"):
        sub.add_parser(name).add_argument("id")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    try:
        plan, parse_problems = load_plan()
        problems = run_checks(plan, parse_problems)
        if args.cmd in ("check", "build"):
            if problems:
                raise PlanError("\n".join(problems))
            print(f"plan ok: {len(plan.goals)} goals, {len(plan.ledger)} ledger rows")
            if args.cmd == "build":
                out = PLAN_DIR / "plan.in.json"
                out.write_text(json.dumps(to_board_plan(plan), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"wrote {out.relative_to(ROOT)}")
                if args.board:
                    build_board(out)
            return 0
        if args.cmd == "list":
            for g in sorted(plan.goals.values(), key=lambda x: x.order):
                print(f"{g.id:8} {g.size} {g.milestone}  {g.title}  ← {', '.join(g.needs) or '—'}")
            return 0
        if args.id not in plan.goals:
            raise PlanError(f"unknown goal '{args.id}'")
        if args.cmd == "show":
            print(render_show(plan, args.id))
        else:
            text = goal_condition(plan, args.id)
            print(text)
            if len(text) > GOAL_CONDITION_LIMIT:
                print(f"\nwarning: {len(text)} chars; /goal accepts {GOAL_CONDITION_LIMIT}. Split the card.", file=sys.stderr)
                return 1
        return 0
    except PlanError as exc:
        print(f"plan error:\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
