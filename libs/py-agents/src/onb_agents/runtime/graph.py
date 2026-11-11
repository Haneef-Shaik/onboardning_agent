"""The agent as a LangGraph subgraph (PRD §7.1 Sub-agents): `agent` ⇄ `tools`, then `finish`.

- `agent`: one guarded model call (`GuardedChatModel.ainvoke`: budget, injection, PII, time limit).
- `tools`: runs the reply's tool calls in order. A bound tool runs through its adapter; any other name
  is forwarded to the hub, which decides (`ToolSet.forward`). Each call is a trajectory step.
- `finish`: the final reply must validate against the agent's output contract; one repair attempt
  through the guards, then `StructuredOutputError` (PRD §8.1 Output). Both outcomes are recorded as
  `guardrail.output.schema` decisions.

Guard errors propagate out of the graph; the runner turns them into a visible result.
"""

from __future__ import annotations

import operator
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
    StructuredOutputError,
    trusted_message,
)
from onb_guardrails.structured import SchemaErrors, parse_structured, repair_prompt

from onb_agents.runtime.tools import ToolSet
from onb_agents.runtime.trajectory import TrajectoryStep, llm_step, tool_step

__all__ = ["AgentState", "build_agent_graph", "recursion_limit"]

AGENT: Final = "agent"
TOOLS: Final = "tools"
FINISH: Final = "finish"


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], operator.add]
    trajectory: Annotated[list[TrajectoryStep], operator.add]
    output: dict[str, Any] | None


def recursion_limit(max_steps: int) -> int:
    """Enough graph steps for `max_steps` model turns, their tool rounds and the finish; the step
    budget, not this limit, is what ends a long run."""
    return 2 * max_steps + 4


def _last_reply(state: AgentState) -> AIMessage:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        raise TypeError("the last message is not a model reply")
    return last


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
        messages: list[BaseMessage] = []
        steps: list[TrajectoryStep] = []
        for call in _last_reply(state).tool_calls:
            message = await self._execute(call)
            messages.append(message)
            steps.append(tool_step(call["name"], call["args"], message))
        return {"messages": messages, "trajectory": steps}

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
        repair = [*state["messages"], trusted_message(repair_prompt(self._contract, parsed))]
        second = await self._guard.ainvoke(repair)
        again = parsed if second.tool_calls else parse_structured(second.text, self._contract)
        if not isinstance(again, SchemaErrors):
            await self._record(GuardrailOutcome.REPAIRED, f"repaired; first={parsed.first_type}")
            return {
                "messages": [second],
                "trajectory": [llm_step(())],
                "output": again.model_dump(mode="json", exclude_none=True),
            }
        reason = f"attempts=2; errors={len(again.errors)}; first={again.first_type}"
        await self._record(GuardrailOutcome.FAILED, reason)
        raise StructuredOutputError(self._contract.__name__, reason)

    async def _record(self, outcome: GuardrailOutcome, reason: str | None) -> None:
        await self._recorder.record(GuardrailDecision(GuardrailCheck.OUTPUT_SCHEMA, outcome, reason))


def _route(state: AgentState) -> str:
    return TOOLS if _last_reply(state).tool_calls else FINISH


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
    graph.add_conditional_edges(AGENT, _route, {TOOLS: TOOLS, FINISH: FINISH})
    graph.add_edge(TOOLS, AGENT)
    graph.add_edge(FINISH, END)
    return graph.compile()
