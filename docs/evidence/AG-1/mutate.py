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
REVIEW = ["libs/py-agents/tests/test_runtime_review_fixes.py"]


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
      "    return TrajectoryStep(kind=\"tool\", name=safe_name(name), args=dict(args), outcome=outcome, reason=reason)",
      "    return TrajectoryStep(kind=\"tool\", name=safe_name(name), outcome=outcome, reason=reason)", BOXES),
    M("M4", "tool steps are recorded in the trajectory", RT + "graph.py",
      "        return {\"messages\": [message], \"trajectory\": [tool_step(call[\"name\"], call[\"args\"], message)]}",
      "        return {\"messages\": [message], \"trajectory\": []}", BOXES),
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
      "        progress = _Progress({\"messages\": initial_messages(p.manifest, p.contract, p.task), \"trajectory\": []})",
      "        leaky = AgentTask(p.task.org_id, p.task.run_id, p.task.task_id, {**p.task.input, \"t\": p.token})\n"
      "        progress = _Progress({\"messages\": initial_messages(p.manifest, p.contract, leaky), \"trajectory\": []})",
      BOXES),
    M("M10", "the grant is hidden from the task's repr", RT + "task.py",
      "    grant: str | None = field(default=None, repr=False)", "    grant: str | None = None", TASK),
    M("M11", "the grant is sent to the hub", RT + "task.py",
      "    if grant:\n        headers[GRANT_HEADER] = grant", "    if False:\n        headers[GRANT_HEADER] = grant",
      BOXES + TASK),
    M("M12", "the failure reason is a code or a type, never the exception text", RT + "runner.py",
      "    return Failure(\"error\", type(error).__name__), None", "    return Failure(\"error\", repr(error)), None",
      BOXES + REVIEW),
    # box 3: budgets end the run visibly
    M("M13", "step budget from the manifest", RT + "runner.py",
      "BudgetLimits(max_steps=limits.max_steps,", "BudgetLimits(max_steps=None,", BOXES),
    M("M14", "an exhausted budget is a failure", RT + "runner.py",
      "        kind: FailureKind = \"timeout\" if \"seconds\" in error.reason else \"budget_exceeded\"\n"
      "        return Failure(kind, error.reason), None",
      "        return None, None", BOXES),
    M("M15", "the run's budget is the task budget's parent", RT + "runner.py",
      "            parent=run_budget,\n", "            parent=None,\n", RUNTIME + REVIEW),
    M("M16", "one deadline over the whole task", RT + "runner.py",
      "            async with asyncio.timeout(p.budget.remaining_seconds()):",
      "            async with asyncio.timeout(None):", RUNTIME + REVIEW),
    M("M17", "a failed run has no output", RT + "runner.py",
      "        output=None if failed else output,", "        output=output,", BOXES + RUNTIME),
    M("M18", "a transport failure is reported by its real cause", RT + "runner.py",
      "        return (real or leaves)[0]", "        return error", REVIEW),
    M("M19", "the state streamed so far survives a failure", RT + "runner.py",
      "            progress.state = cast(AgentState, state)", "            pass", BOXES + REVIEW),
    # guards around every model call
    M("M20", "every model call goes through the guards", RT + "graph.py",
      "        reply = await self._guard.ainvoke(state[\"messages\"])",
      "        reply = await self._guard._model.ainvoke(state[\"messages\"])  # type: ignore[attr-defined]", RUNTIME),
    M("M21", "the task input is untrusted (not a trusted message)", RT + "prompt.py",
      "trusted_message(instruction), HumanMessage(data)]", "trusted_message(instruction), trusted_message(data)]",
      RUNTIME),
    M("M22", "an injection is a contract refusal", RT + "runner.py",
      "        return None, INJECTION_REFUSAL", "        return Failure(\"error\", \"injection\"), None", RUNTIME),
    M("M23", "the guarded calls run in the task org's tenant scope", RT + "runner.py",
      "        with tenant_scope(task.org_id):\n", "        if True:\n", BOXES),
    # output contract, repair
    M("M24", "the final reply is validated against the contract", RT + "graph.py",
      "        if not isinstance(parsed, SchemaErrors):\n            await self._record(GuardrailOutcome.PASSED, None)",
      "        if True:\n            await self._record(GuardrailOutcome.PASSED, None)\n"
      "            return {\"output\": {\"raw\": reply.text}}", RUNTIME),
    M("M25", "one repair, then a visible failure", RT + "graph.py",
      "        return {**turn, \"schema_failure\": reason}", "        return {**turn, \"output\": {\"status\": \"ok\"}}",
      RUNTIME + REVIEW),
    M("M26", "a schema failure is an invalid_output", RT + "runner.py",
      "        if schema_failure:\n", "        if False:\n", RUNTIME + REVIEW),
    M("M27", "a repair needs a step left", RT + "graph.py",
      "        if self._guard.budget.exceeded(before_call=True) is not None:", "        if False:", REVIEW),
    M("M28", "the repair prompt never echoes model-made keys", RT + "graph.py",
      "    return SchemaErrors(tuple((safe(loc), kind, message) for loc, kind, message in errors.errors))",
      "    return errors", REVIEW),
    # tool rounds
    M("M29", "the per-turn tool-call cap", RT + "graph.py",
      "        if answered >= MAX_TOOL_CALLS_PER_TURN:", "        if False:", REVIEW),
    M("M30", "every call of a turn is answered, one per visit", RT + "graph.py",
      "    return TOOLS if answered < len(reply.tool_calls) else AGENT", "    return AGENT", REVIEW + RUNTIME),
    M("M31", "model-chosen tool names are recorded only in a safe shape", RT + "trajectory.py",
      "    return name if _NAME.fullmatch(name) else INVALID_NAME", "    return name", REVIEW),
    # local tools
    M("M32", "only the local tools the manifest lists", RT + "local_tools.py",
      "    return [LOCAL_TOOLS[name] for name in manifest.tools]", "    return list(LOCAL_TOOLS.values())", LOCAL),
    M("M33", "render never re-renders substituted values", RT + "local_tools.py",
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), args.template)",
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), args.template)\n"
      "    text = _PLACEHOLDER.sub(_substitute(args.variables, missing), text)", LOCAL),
    M("M34", "render placeholders are plain names", RT + "local_tools.py",
      "        name = _NAME.fullmatch(match.group(1))", "        name = _NAME.match(match.group(1))", LOCAL),
    M("M35", "date_calc bounds", RT + "local_tools.py", "Field(ge=-MAX_DAYS, le=MAX_DAYS,", "Field(", LOCAL),
    M("M36", "bad local-tool arguments are an error message", RT + "local_tools.py",
      "        handle_validation_error=on_invalid,\n", "", LOCAL),
    M("M37", "a date out of range is an error message", RT + "local_tools.py",
      "        except OverflowError:", "        except ZeroDivisionError:", LOCAL),
    # box 4: the output contracts
    M("M38", "an ok answer carries its payload", OUT, "            if payload is None:\n", "            if False:\n",
      CONTRACTS),
    M("M39", "contracts forbid unknown fields", OUT,
      "    model_config = ConfigDict(extra=\"forbid\", frozen=True, hide_input_in_errors=True)",
      "    model_config = ConfigDict(extra=\"ignore\", frozen=True, hide_input_in_errors=True)", CONTRACTS),
    M("M40", "the status is one of ok | refused | needs_human", OUT,
      "AgentStatus = Literal[\"ok\", \"refused\", \"needs_human\"]", "AgentStatus = str", CONTRACTS),
    M("M41", "the approval recommendation is an enum", OUT,
      "    recommendation: Literal[\"approve\", \"reject\", \"needs_human\"]", "    recommendation: StrictStr", CONTRACTS),
    M("M42", "the run assistant's refusal fills `answer`", OUT,
      "        return {\"status\": \"refused\", \"answer\": explanation}", "        return {\"status\": \"refused\"}",
      CONTRACTS + RUNTIME),
    M("M43", "the mode selects the contract", OUT,
      "    return agent if mode in (None, DEFAULT_MODE) else f\"{agent}{MODE_SEPARATOR}{mode}\"",
      "    return agent", CONTRACTS + RUNTIME),
    M("M44", "a refusal or hand-off explains itself", OUT,
      "        if getattr(self, self.EXPLAINED_BY) is None:", "        if False:", CONTRACTS),
    M("M45", "a refusal carries no payload", OUT,
      "        if self.status == \"refused\" and self.PAYLOAD != self.EXPLAINED_BY and payload is not None:",
      "        if False:", CONTRACTS),
    M("M46", "a DB answer never names an org", OUT, "    if reserved:\n", "    if False:\n", CONTRACTS),
    M("M47", "a message recipient is a ptk_ token", OUT,
      "    recipient_token: PiiToken\n", "    recipient_token: Text\n", CONTRACTS),
    M("M48", "task-update inputs are bounded", OUT,
      "        return None if value is None else bounded_json(value, depth=MAX_INPUT_DEPTH, size=MAX_INPUTS_SIZE)",
      "        return value", CONTRACTS),
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
