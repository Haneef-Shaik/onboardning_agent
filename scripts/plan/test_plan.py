"""Tests for scripts/plan/plan.py. Run: python3 -m unittest scripts/plan/test_plan.py"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan as p  # noqa: E402

CONFIG = '```json\n{"title": "T", "owner": "Haneef", "people": [{"name": "Haneef"}], "tracks": {"M0": "Zero"}}\n```\n'


def card(gid: str, needs: str = "—", kind: str = "Done when", prd: str = "§1.", boxes: tuple[str, ...] = ("a box",)) -> str:
    box_lines = "\n".join(f"- [ ] {b}" for b in boxes)
    return (
        f"### {gid} · Title of {gid}\n"
        f"- **Size:** S · **Milestone:** M0 · **Label:** {gid.lower()}\n"
        f"- **Needs:** {needs}\n\n"
        f"**Outcome.** Something true.\n\n"
        f"**PRD.** {prd}\n\n"
        f"**{kind}.**\n{box_lines}\n\n"
    )


def goals_doc(*cards: str) -> str:
    return CONFIG + "\n## M0 · Zero\n\n" + "".join(cards)


LEDGER_HEAD = "| ID | Requirement | Proved by | Gate | Result |\n|---|---|---|---|---|\n"


def problems_for(goals_text: str, ledger_text: str = LEDGER_HEAD, prd_text: str = "") -> list[str]:
    plan, parse_problems = p.load_plan(goals_text, ledger_text)
    return p.run_checks(plan, parse_problems, prd_text)


class ParseTests(unittest.TestCase):
    def test_parses_card_fields(self) -> None:
        plan, problems = p.load_plan(goals_doc(card("A-1"), card("A-2", needs="A-1", boxes=("x", "y"))), LEDGER_HEAD)
        self.assertEqual(problems, [])
        g = plan.goals["A-2"]
        self.assertEqual(g.needs, ("A-1",))
        self.assertEqual(g.boxes, ("x", "y"))
        self.assertEqual(g.box_kind, "Done when")
        self.assertEqual(g.outcome, "Something true.")

    def test_anchor_matches_github_style(self) -> None:
        self.assertEqual(p.github_anchor("F-1 · Create the Nx monorepo"), "f-1--create-the-nx-monorepo")


class CheckTests(unittest.TestCase):
    def test_valid_plan_has_no_problems(self) -> None:
        self.assertEqual(problems_for(goals_doc(card("A-1"), card("MS-0", needs="A-1", kind="Exit checks"))), [])

    def test_missing_need_is_reported(self) -> None:
        self.assertIn("A-1: needs 'Z-9', which is not a goal", problems_for(goals_doc(card("A-1", needs="Z-9"))))

    def test_cycle_is_reported(self) -> None:
        found = problems_for(goals_doc(card("A-1", needs="A-2"), card("A-2", needs="A-1")))
        self.assertTrue(any(x.startswith("cycle:") for x in found), found)

    def test_card_without_boxes_is_reported(self) -> None:
        found = problems_for(goals_doc(card("A-1", boxes=())))
        self.assertIn("A-1: has no contract (no '- [ ]' boxes under Done when / Exit checks)", found)

    def test_milestone_needs_exit_checks(self) -> None:
        self.assertIn("MS-0: a milestone card needs **Exit checks.**", problems_for(goals_doc(card("MS-0"))))

    def test_uncovered_fr_is_reported(self) -> None:
        found = problems_for(goals_doc(card("A-1", prd="FR-X1.")), prd_text="FR-X1 and FR-X2")
        self.assertEqual(found, ["PRD FR-X2 is not named on any card's **PRD.** line"])

    def test_ledger_gate_must_wait_on_provers(self) -> None:
        doc = goals_doc(card("A-1"), card("A-2"), card("MS-0", needs="A-1", kind="Exit checks"))
        ledger = LEDGER_HEAD + "| R1 | req | A-1, A-2 | MS-0 | open |\n"
        self.assertIn("ledger R1: gate MS-0 does not wait on A-2", problems_for(doc, ledger))

    def test_ledger_result_format(self) -> None:
        doc = goals_doc(card("A-1"), card("MS-0", needs="A-1", kind="Exit checks"))
        good = LEDGER_HEAD + "| R1 | req | A-1 | MS-0 | pass · 2026-11-10 · compose · docs/evidence/A-1/x.md |\n"
        bad = LEDGER_HEAD + "| R1 | req | A-1 | MS-0 | passed |\n"
        self.assertEqual(problems_for(doc, good), [])
        self.assertIn("ledger R1: malformed result 'passed'", problems_for(doc, bad))

    def test_prod_gate_needs_no_provers(self) -> None:
        ledger = LEDGER_HEAD + "| D1 | legal | — | PROD | open |\n"
        self.assertEqual(problems_for(goals_doc(card("A-1")), ledger), [])


class OutputTests(unittest.TestCase):
    def test_goal_condition_lists_every_box_and_last_prover_rows(self) -> None:
        doc = goals_doc(card("A-1", boxes=("first box", "second box")), card("MS-0", needs="A-1", kind="Exit checks"))
        ledger = LEDGER_HEAD + "| R1 | req | A-1 | MS-0 | open |\n"
        plan, _ = p.load_plan(doc, ledger)
        text = p.goal_condition(plan, "A-1")
        self.assertTrue(text.startswith('/goal Goal A-1 "Title of A-1"'))
        self.assertIn("[1] first box [2] second box", text)
        self.assertIn("ACCEPTANCE.md rows R1", text)
        self.assertIn("goal/a-1-a-1", text)

    def test_board_plan_shape(self) -> None:
        plan, _ = p.load_plan(goals_doc(card("A-1"), card("A-2", needs="A-1")), LEDGER_HEAD)
        board = p.to_board_plan(plan)
        self.assertEqual([g["id"] for g in board["goals"]], ["A-1", "A-2"])
        self.assertEqual(board["goals"][1]["needs"], ["A-1"])
        self.assertEqual(board["goals"][0]["owner"], "Haneef")


class RealPlanTests(unittest.TestCase):
    """The committed plan must stay valid, and every /goal condition must fit."""

    def test_repo_plan_is_valid(self) -> None:
        plan, parse_problems = p.load_plan()
        self.assertEqual(p.run_checks(plan, parse_problems), [])

    def test_every_goal_condition_fits_the_goal_limit(self) -> None:
        plan, _ = p.load_plan()
        for gid in plan.goals:
            with self.subTest(goal=gid):
                self.assertLessEqual(len(p.goal_condition(plan, gid)), p.GOAL_CONDITION_LIMIT)


if __name__ == "__main__":
    unittest.main()
