#!/usr/bin/env bash
# AG-1 Done-when evidence: the named tests for each box. Run from the repo root.
set -u
run() { echo "== $1"; shift; uv run --frozen pytest "$@" -v -p no:cacheprovider --no-header 2>&1 | grep -E "PASSED|FAILED|ERROR|passed|failed"; }
run "box 1: a tool outside the manifest is denied by the hub; the denial is in the trajectory (fake connector)" \
  "libs/py-agents/tests/test_runtime_boxes.py::test_box1_a_tool_outside_the_manifest_is_denied_by_the_hub_and_the_denial_is_in_the_trajectory" \
  "libs/py-agents/tests/test_runtime_boxes.py::test_box1_a_tool_the_agent_was_never_given_is_still_sent_to_the_hub_and_denied"
run "box 2: the grant and agent token never appear in captured LLM input" \
  libs/py-agents/tests/test_runtime_boxes.py -k box2
run "box 3: an exhausted step budget ends the run with a visible failure" \
  "libs/py-agents/tests/test_runtime_boxes.py::test_box3_an_exhausted_step_budget_ends_the_run_with_a_visible_failure"
run "box 4: every agent output contract has a schema test" \
  libs/py-contracts/tests/test_agent_output_contracts.py -q
run "box 4: every registered agent has a contract and runs from its manifest" \
  libs/py-agents/tests/test_runtime.py -k every_registered_agent
