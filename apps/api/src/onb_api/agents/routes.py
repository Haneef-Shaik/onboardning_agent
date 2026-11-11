"""`GET /agents`, `GET /agents/{name}`: the agent registry for HR_ADMIN (PRD U8, FR-A1, §10; REG-1).

HR and MANAGER get 403 before the registry is read. The registry is global, so no parameter names an
org and records carry none. An unknown name is 404; a name outside the registry's grammar is 422.

UI-8 (FR-A2): with an LLM configuration, each record also carries `resolved_llm`, the connector and
model the manifest's choice resolves to (`onb_llm.resolve_llm`, as the agent runtime resolves it), and
never the provider params llm.yaml routes through it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from onb_agents.registry import RegisteredAgent
from onb_contracts import Envelope, ErrorCode
from onb_contracts.agents import (
    AGENT_NAME_PATTERN,
    AgentEnvelope,
    AgentLimits,
    AgentLLM,
    AgentMcpGrant,
    AgentRecord,
    AgentResolvedLLM,
    AgentsEnvelope,
)
from onb_contracts.agents import AgentCollections as Collections
from onb_contracts.auth import Role
from onb_llm import LLMConfig, LLMConfigError, LLMSelection, resolve_llm

from onb_api.agents.reader import AgentRegistryReader
from onb_api.auth.dependencies import require_role
from onb_api.auth.tokens import Principal
from onb_api.errors import AppError
from onb_api.responses import respond

__all__ = ["agent_registry", "router"]

router = APIRouter(tags=["agents"])
_log = logging.getLogger(__name__)

HrAdmin = Annotated[Principal, Depends(require_role(Role.HR_ADMIN))]


def agent_registry(request: Request) -> AgentRegistryReader:
    registry: AgentRegistryReader | None = request.app.state.agents
    if registry is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE)
    return registry


Registry = Annotated[AgentRegistryReader, Depends(agent_registry)]


def llm_config(request: Request) -> LLMConfig | None:
    config: LLMConfig | None = request.app.state.llm
    return config


Llm = Annotated[LLMConfig | None, Depends(llm_config)]


def _resolved(agent: RegisteredAgent, config: LLMConfig | None) -> AgentResolvedLLM | None:
    if config is None:
        return None
    llm = agent.manifest.llm
    selection = LLMSelection(alias=llm.alias, connector=llm.connector, model=llm.model, params=llm.params.values())
    try:
        chosen = resolve_llm(config, manifest=selection).task_record()
    except LLMConfigError:
        # One manifest naming an alias llm.yaml lacks must not hide every other agent: shown unresolved.
        _log.warning("agent %s: its model choice does not resolve against llm.yaml", agent.name)
        return None
    return AgentResolvedLLM.model_validate(chosen)


def _record(agent: RegisteredAgent, config: LLMConfig | None) -> AgentRecord:
    manifest = agent.manifest
    return AgentRecord(
        name=agent.name,
        version=agent.version,
        active=agent.active,
        purpose=manifest.purpose,
        llm=AgentLLM(
            alias=manifest.llm.alias,
            connector=manifest.llm.connector,
            model=manifest.llm.model,
            params=manifest.llm.params.values(),
        ),
        mcp={
            server: AgentMcpGrant(
                tools=list(grant.tools),
                collections=None
                if grant.collections is None
                else Collections(read=list(grant.collections.read), write=list(grant.collections.write)),
            )
            for server, grant in manifest.mcp.items()
        },
        tools=list(manifest.tools),
        memory_scopes=list(manifest.memory_scopes),
        requires_approval=list(manifest.requires_approval),
        limits=AgentLimits.model_validate(manifest.limits.model_dump()),
        sha256=agent.sha256,
        updated_at=agent.updated_at,
        resolved_llm=_resolved(agent, config),
    )


@router.get("/agents", response_model=AgentsEnvelope, operation_id="listAgents")
async def list_agents(_: HrAdmin, registry: Registry, llm: Llm) -> Envelope[list[AgentRecord]]:
    return respond([_record(agent, llm) for agent in await registry.list_agents()])


@router.get("/agents/{name}", response_model=AgentEnvelope, operation_id="getAgent")
async def get_agent(
    _: HrAdmin, registry: Registry, llm: Llm, name: Annotated[str, Path(max_length=64, pattern=AGENT_NAME_PATTERN)]
) -> Envelope[AgentRecord]:
    agent = await registry.get_agent(name)
    if agent is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return respond(_record(agent, llm))
