"""AG-1 mutation checks: break each guard on purpose, run its tests, record which named test failed,
restore the file byte for byte. Run from the repo root: `uv run --frozen python docs/evidence/AG-1/mutate.py`."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RT = "libs/py-agents/src/onb_agents/runtime/"
OUT = "libs/py-contracts/src/onb_contracts/agent_outputs.py"
BOXES = ["libs/py-agents/tests/test_runtime_boxes.py"]
RUNTIME = ["libs/py-agents/tests/test_runtime.py"]
TASK = ["libs/py-agents/tests/test_task_context.py"]
LOCAL = ["libs/py-agents/tests/test_local_tools.py"]
CONTRACTS = ["libs/py-contracts/tests/test_agent_output_contracts.py"]


@dataclass(frozen=True)
class Mutation:
    id: str
    guard: str
    path: str
    old: str
    new: str
    tests: list[str]


M = Mutation
MUTATIONS = [
    # box 1: the hub decides every call, and its denial is a trajectory step
    M("M1", "unbound names are forwarded to the hub", RT + "graph.py",
      "            forwarded = await self._tools.forward(call[\"name\"], call[\"args\"])\n"
      "            status = \"error\" if forwarded.is_error else \"success\"\n"
      "            return ToolMessage(content=forwarded.text, tool_call_id=call_id, name=call[\"name\"], status=status)",
      "            return ToolMessage(content=\"not allowed\", tool_call_id=call_id, name=call[\"name\"], status=\"error\")",
      BOXES),
    M("M2", "a hub denial is classified as denied with its reason", RT + "trajectory.py",
      "    denial = _DENIAL.match(message.text.strip())", "    denial = None", BOXES),
    M("M3", "a tool step records its arguments", RT + "trajectory.py",
      "        return TrajectoryStep(kind=\"tool\", name=name, args=dict(args), outcome=\"denied\", reason=denial.group(1))",
      "        return TrajectoryStep(kind=\"tool\", name=name, outcome=\"denied\", reason=denial.group(1))", BOXES),
    M("M4", "tool steps are recorded in the trajectory", RT + "graph.py",
      "            steps.append(tool_step(call[\"name\"], call[\"args\"], message))", "            pass", BOXES),
    M("M5", "only the manifest's hub tools are bound", RT + "tools.py",
      "yield ToolSet(bound=tuple(t for t in loaded if t.name in allowed), forward=forward)",
      "yield ToolSet(bound=tuple(loaded), forward=forward)", RUNTIME),
    M("M6", "token scopes are the manifest's tools only", RT + "task.py",
      "    tools = [hub_tool_name(server, tool) for server, grant in manifest.mcp.items() for tool in grant.tools]",
      "    tools = [hub_tool_name(server, tool) for server, grant in manifest.mcp.items() for tool in grant.tools]"
      " + [\"mongo__insert_one\"]", TASK),
    M("M7", "an approval scope only for a required rule", RT + "task.py",
      "    if unknown:\n        raise ValueError(f\"{', '.join(unknown)} is not a rule",
      "    if False:\n        raise ValueError(f\"{', '.join(unknown)} is not a rule", TASK),
    # box 2: the token and the grant stay out of the model's input
    M("M8", "the grant travels only in a header (leaked into the instruction)", RT + "prompt.py",
      "    instruction = TASK_TEMPLATE.format(run_id=task.run_id, task_id=task.task_id, mode=mode)",
      "    instruction = TASK_TEMPLATE.format(run_id=task.run_id, task_id=task.task_id, mode=mode) + str(task.grant)",
      BOXES),
    M("M9", "the token travels only in a header (leaked into the task input)", RT + "runner.py",
      "                return await self._drive(manifest, contract, task, toolset, choice, local, budget, context)",
      "                task = AgentTask(task.org_id, task.run_id, task.task_id, {**task.input, \"t\": token}, task.mode,"
      " task.approvals, task.grant)\n"
      "                return await self._drive(manifest, contract, task, toolset, choice, local, budget, context)",
      BOXES),
    M("M10", "the grant is hidden from the task's repr", RT + "task.py",
      "    grant: str | None = field(default=None, repr=False)", "    grant: str | None = None", TASK),
    M("M11", "the grant is sent to the hub", RT + "task.py",
      "    if grant:\n        headers[GRANT_HEADER] = grant", "    if False:\n        headers[GRANT_HEADER] = grant",
      BOXES + TASK),
    # box 3: budgets end the run visibly
    M("M12", "step budget from the manifest", RT + "runner.py",
      "BudgetLimits(max_steps=limits.max_steps,", "BudgetLimits(max_steps=None,", BOXES),
    M("M13", "an exhausted budget is a failure", RT + "runner.py",
      "            return _Ended(state=last, failure=Failure(\"budget_exceeded\", error.reason))",
      "            return _Ended(state=last)", BOXES),
    M("M14", "the run's budget is the task budget's parent", RT + "runner.py",
      "            parent=run_budget,\n", "            parent=None,\n", RUNTIME),
    M("M15", "the task time limit", RT + "runner.py",
      "            async with asyncio.timeout(manifest.limits.timeout_s):",
      "            async with asyncio.timeout(None):", RUNTIME),
    M("M16", "a failed run has no output", RT + "runner.py",
      "        output=None if failed else output,", "        output=output,", BOXES + RUNTIME),
    # guards around every model call, contracts
    M("M17", "every model call goes through the guards", RT + "graph.py",
      "        reply = await self._guard.ainvoke(state[\"messages\"])",
      "        reply = await self._guard._model.ainvoke(state[\"messages\"])  # type: ignore[attr-defined]", RUNTIME),
    M("M18", "the task input is untrusted (not a trusted message)", RT + "prompt.py",
      "trusted_message(instruction), HumanMessage(data)]", "trusted_message(instruction), trusted_message(data)]",
      RUNTIME),
    M("M19", "an injection is a contract refusal", RT + "runner.py",
      "            return _Ended(state=last, refusal=INJECTION_REFUSAL)",
      "            return _Ended(state=last, failure=Failure(\"error\", \"injection\"))", RUNTIME),
    M("M20", "the final reply is validated against the contract", RT + "graph.py",
      "        if not isinstance(parsed, SchemaErrors):\n            await self._record(GuardrailOutcome.PASSED, None)",
      "        if True:\n            await self._record(GuardrailOutcome.PASSED, None)\n"
      "            return {\"output\": {\"raw\": reply.text}}", RUNTIME),
    M("M21", "one repair, then a visible failure", RT + "graph.py",
      "        raise StructuredOutputError(self._contract.__name__, reason)", "        return {\"output\": None}",
      RUNTIME),
    M("M22", "the guarded calls run in the task org's tenant scope", RT + "runner.py",
      "        with tenant_scope(task.org_id):\n", "        if True:\n", BOXES),
    M("M23", "only the local tools the manifest lists", RT + "local_tools.py",
      "    return [LOCAL_TOOLS[name] for name in manifest.tools]", "    return list(LOCAL_TOOLS.values())", LOCAL),
    M("M24", "render never re-renders substituted values", RT + "local_tools.py",
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), args.template)",
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), args.template)\n"
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), text)", LOCAL),
    M("M25", "render placeholders are plain names", RT + "local_tools.py",
      "        name = _NAME.fullmatch(match.group(1))", "        name = _NAME.match(match.group(1))", LOCAL),
    M("M26", "date_calc bounds", RT + "local_tools.py",
      "Field(ge=-MAX_DAYS, le=MAX_DAYS,", "Field(", LOCAL),
    # box 4: the output contracts
    M("M27", "an ok answer carries its payload", OUT,
      "        if self.status == \"ok\" and getattr(self, self.PAYLOAD) is None:", "        if False:", CONTRACTS),
    M("M28", "contracts forbid unknown fields", OUT,
      "    model_config = ConfigDict(extra=\"forbid\", frozen=True, hide_input_in_errors=True)",
      "    model_config = ConfigDict(extra=\"ignore\", frozen=True, hide_input_in_errors=True)", CONTRACTS),
    M("M29", "the status is one of ok | refused | needs_human", OUT,
      "AgentStatus = Literal[\"ok\", \"refused\", \"needs_human\"]", "AgentStatus = str", CONTRACTS),
    M("M30", "the approval recommendation is an enum", OUT,
      "    recommendation: Literal[\"approve\", \"reject\", \"needs_human\"]", "    recommendation: StrictStr", CONTRACTS),
    M("M31", "the run assistant's refusal fills `answer`", OUT,
      "        return {\"status\": \"refused\", \"answer\": explanation}", "        return {\"status\": \"refused\"}",
      CONTRACTS + RUNTIME),
    M("M32", "the mode selects the contract", OUT,
      "    return agent if mode in (None, DEFAULT_MODE) else f\"{agent}{MODE_SEPARATOR}{mode}\"",
      "    return agent", CONTRACTS + RUNTIME),
]


def run(mutation: Mutation) -> tuple[bool, list[str]]:
    path = ROOT / mutation.path
    original = path.read_bytes()
    text = original.decode()
    if text.count(mutation.old) != 1:
        raise SystemExit(f"{mutation.id}: the guard text is not unique in {mutation.path}")
    path.write_text(text.replace(mutation.old, mutation.new))
    try:
        failed: list[str] = []
        for target in mutation.tests:
            proc = subprocess.run(  # noqa: S603
                ["uv", "run", "--frozen", "pytest", target, "-q", "-p", "no:cacheprovider", "--no-header"],  # noqa: S607
                cwd=ROOT, capture_output=True, text=True, timeout=900, check=False,
            )
            failed += re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.MULTILINE)
        return bool(failed), failed
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original


def main() -> None:
    only = set(sys.argv[1:])
    mutations = [m for m in MUTATIONS if not only or m.id in only]
    caught = 0
    for mutation in mutations:
        killed, failed = run(mutation)
        caught += killed
        status = "CAUGHT" if killed else "SURVIVED"
        print(f"{mutation.id} {status}: {mutation.guard} ({mutation.path})")
        for name in failed[:3]:
            print(f"    FAILED {name}")
    print(f"SUMMARY: {caught} of {len(mutations)} mutations caught")


if __name__ == "__main__":
    main()
