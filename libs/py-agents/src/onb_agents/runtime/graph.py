"""The agent as a LangGraph subgraph (PRD §7.1 Sub-agents): `agent` ⇄ `tools`, then `finish`.

- `agent`: one guarded model call (`GuardedChatModel.ainvoke`: budget, injection, PII, time limit).
- `tools`: runs **one** tool call of the last reply per visit, so every call that ran is in the state
  (and the trajectory) even if a later one fails. A bound tool runs through its adapter; any other
  name is forwarded to the hub, which decides (`ToolSet.forward`). Calls past
  `MAX_TOOL_CALLS_PER_TURN` in one reply are answered with an error and never run.
- `finish`: the final reply must validate against the agent's output contract; one repair attempt
  through the guards (when the step budget leaves room for it), else `schema_failure` is set (PRD §8.1
  Output). Both outcomes are `guardrail.output.schema` decisions. The repair prompt never echoes a key
  the model invented: error locations are reduced to plain field names.

Guard errors propagate out of the graph; the runner turns them into a visible result.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from onb_contracts.agent_outputs import AgentOutput
from onb_guardrails import (
    DecisionRecorder,
    GuardedChatModel,
    GuardrailCheck,
    GuardrailDecision,
    GuardrailOutcome,
    trusted_message,
)
from onb_guardrails.structured import SchemaErrors, parse_structured, repair_prompt

from onb_agents.runtime.tools import ToolSet
from onb_agents.runtime.trajectory import TrajectoryStep, capped_step, llm_step, tool_step

__all__ = ["MAX_TOOL_CALLS_PER_TURN", "AgentState", "build_agent_graph", "recursion_limit"]

AGENT: Final = "agent"
TOOLS: Final = "tools"
FINISH: Final = "finish"
MAX_TOOL_CALLS_PER_TURN: Final = 8
TOO_MANY_CALLS: Final = f"too many tool calls in one turn (at most {MAX_TOOL_CALLS_PER_TURN}); this one was not run"
_FIELD: Final = re.compile(r"[A-Za-z0-9_]{1,64}")


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], operator.add]
    trajectory: Annotated[list[TrajectoryStep], operator.add]
    output: dict[str, Any] | None
    schema_failure: str | None


def recursion_limit(max_steps: int) -> int:
    """Graph steps for `max_steps` model turns, each with up to the per-turn cap of tool calls (one
    visit each, plus one for the over-cap answers), and the finish; the budgets end a run first."""
    return max_steps * (MAX_TOOL_CALLS_PER_TURN + 2) + 4


def _last_reply(state: AgentState) -> AIMessage:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        raise TypeError("the last message is not a model reply")
    return last


def _pending(state: AgentState) -> tuple[AIMessage, int]:
    """The last model reply and how many of its tool calls are already answered."""
    answered = 0
    for message in reversed(state["messages"]):
        if isinstance(message, AIMessage):
            return message, answered
        if isinstance(message, ToolMessage):
            answered += 1
    raise TypeError("no model reply in the state")


def _sanitised(errors: SchemaErrors) -> SchemaErrors:
    """Error locations reduced to plain field names: a key the model made up becomes `<key>`."""

    def safe(location: str) -> str:
        return ".".join(part if _FIELD.fullmatch(part) else "<key>" for part in location.split(".")) if location else ""

    return SchemaErrors(tuple((safe(loc), kind, message) for loc, kind, message in errors.errors))


class _Nodes:
    def __init__(
        self,
        guard: GuardedChatModel,
        tools: ToolSet,
        local: Sequence[BaseTool],
        contract: type[AgentOutput],
        recorder: DecisionRecorder,
    ) -> None:
        self._guard = guard
        self._tools = tools
        self._bound: Mapping[str, BaseTool] = {t.name: t for t in (*tools.bound, *local)}
        self._contract = contract
        self._recorder = recorder

    async def agent(self, state: AgentState) -> AgentState:
        reply = await self._guard.ainvoke(state["messages"])
        return {"messages": [reply], "trajectory": [llm_step(tuple(c["name"] for c in reply.tool_calls))]}

    async def tools(self, state: AgentState) -> AgentState:
        reply, answered = _pending(state)
        calls = reply.tool_calls
        if answered >= MAX_TOOL_CALLS_PER_TURN:  # answer every call past the cap at once, run none
            rest = calls[answered:]
            messages: list[BaseMessage] = [_tool_error(call, TOO_MANY_CALLS) for call in rest]
            return {"messages": messages, "trajectory": [capped_step(c["name"], c["args"]) for c in rest]}
        call = calls[answered]
        message = await self._execute(call)
        return {"messages": [message], "trajectory": [tool_step(call["name"], call["args"], message)]}

    async def _execute(self, call: ToolCall) -> ToolMessage:
        call_id = call.get("id") or ""
        tool = self._bound.get(call["name"])
        if tool is None:  # never given to the agent: the hub decides, and audits it
            forwarded = await self._tools.forward(call["name"], call["args"])
            status = "error" if forwarded.is_error else "success"
            return ToolMessage(content=forwarded.text, tool_call_id=call_id, name=call["name"], status=status)
        result = await tool.ainvoke({**call, "type": "tool_call", "id": call_id})
        if not isinstance(result, ToolMessage):
            raise TypeError(f"tool {call['name']} returned {type(result).__name__}, not a ToolMessage")
        return result

    async def finish(self, state: AgentState) -> AgentState:
        reply = _last_reply(state)
        parsed = parse_structured(reply.text, self._contract)
        if not isinstance(parsed, SchemaErrors):
            await self._record(GuardrailOutcome.PASSED, None)
            return {"output": parsed.model_dump(mode="json", exclude_none=True)}
        if self._guard.budget.exceeded(before_call=True) is not None:
            reason = f"attempts=1; no_repair_budget; first={parsed.first_type}"
            await self._record(GuardrailOutcome.FAILED, reason)
            return {"schema_failure": reason}
        return await self._repair(state, parsed)

    async def _repair(self, state: AgentState, first: SchemaErrors) -> AgentState:
        prompt = trusted_message(repair_prompt(self._contract, _sanitised(first)))
        second = await self._guard.ainvoke([*state["messages"], prompt])
        again = first if second.tool_calls else parse_structured(second.text, self._contract)
        turn: AgentState = {"messages": [prompt, second], "trajectory": [llm_step(())]}
        if not isinstance(again, SchemaErrors):
            await self._record(GuardrailOutcome.REPAIRED, f"repaired; first={first.first_type}")
            return {**turn, "output": again.model_dump(mode="json", exclude_none=True)}
        reason = f"attempts=2; errors={len(again.errors)}; first={again.first_type}"
        await self._record(GuardrailOutcome.FAILED, reason)
        return {**turn, "schema_failure": reason}

    async def _record(self, outcome: GuardrailOutcome, reason: str | None) -> None:
        await self._recorder.record(GuardrailDecision(GuardrailCheck.OUTPUT_SCHEMA, outcome, reason))


def _tool_error(call: ToolCall, text: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call.get("id") or "", name=call["name"], status="error")


def _after_agent(state: AgentState) -> str:
    return TOOLS if _last_reply(state).tool_calls else FINISH


def _after_tools(state: AgentState) -> str:
    reply, answered = _pending(state)
    return TOOLS if answered < len(reply.tool_calls) else AGENT


def build_agent_graph(
    *,
    guard: GuardedChatModel,
    tools: ToolSet,
    local: Sequence[BaseTool],
    contract: type[AgentOutput],
    recorder: DecisionRecorder,
) -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
    """The compiled subgraph for one task; usable on its own or as a node of a run graph."""
    nodes = _Nodes(guard, tools, local, contract, recorder)
    graph = StateGraph(AgentState)
    graph.add_node(AGENT, nodes.agent)
    graph.add_node(TOOLS, nodes.tools)
    graph.add_node(FINISH, nodes.finish)
    graph.add_edge(START, AGENT)
    graph.add_conditional_edges(AGENT, _after_agent, {TOOLS: TOOLS, FINISH: FINISH})
    graph.add_conditional_edges(TOOLS, _after_tools, {TOOLS: TOOLS, AGENT: AGENT})
    graph.add_edge(FINISH, END)
    return graph.compile()
