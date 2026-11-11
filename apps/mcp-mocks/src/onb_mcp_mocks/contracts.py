"""The JSON-schema contracts of the mock tools (PRD §2 Non-goals, §7 `mcp-mocks`).

The tool names are the ones REG-1's `config/agents/provisioning_agent.yaml` grants under
`mcp.mocks`. Argument models are closed (`additionalProperties: false`), so an agent cannot add
`org_id` or anything else. Every tool takes `task_id`: the run task's id is the idempotency key, so
a retried task never creates a second account or order. Ids are 24-hex ObjectIds; every other
argument is a lowercase slug, so no free text (and no raw PII) reaches a mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

__all__ = [
    "CONTRACTS",
    "TOOL_NAMES",
    "BitbucketAccount",
    "CreateBitbucketAccountArgs",
    "LaptopOrder",
    "LaptopProfile",
    "MockContract",
    "ProvisionLaptopArgs",
]

HEX_ID = r"^[0-9a-f]{24}$"
SLUG = r"^[a-z][a-z0-9-]{0,62}$"
MAX_GROUPS = 10

TaskId = Annotated[
    str,
    Field(
        pattern=HEX_ID,
        description="The run task's id (24-hex). It is the idempotency key: the same task_id returns the same result.",
    ),
]
HireId = Annotated[str, Field(pattern=HEX_ID, description="The new hire's id (24-hex)")]
Slug = Annotated[str, Field(pattern=SLUG, description="Lowercase letters, digits and dashes")]
LaptopProfile = Literal["standard", "engineering", "design"]


def _unique(values: list[str]) -> list[str]:
    """Groups are a set: no repeats, and sorted, so their order never makes a replay a conflict."""
    if len(set(values)) != len(values):
        raise ValueError("groups must not repeat")
    return sorted(values)


class _Args(ArgModelBase):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)


class CreateBitbucketAccountArgs(_Args):
    task_id: TaskId
    hire_id: HireId
    workspace: Slug
    groups: Annotated[list[Slug], Field(max_length=MAX_GROUPS), AfterValidator(_unique)] = Field(default_factory=list)


class ProvisionLaptopArgs(_Args):
    task_id: TaskId
    hire_id: HireId
    profile: LaptopProfile
    office: Slug


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BitbucketAccount(_Result):
    account_id: str
    hire_id: str
    username: str
    workspace: str
    groups: list[str]
    status: Literal["active"]


class LaptopOrder(_Result):
    order_id: str
    hire_id: str
    profile: LaptopProfile
    model: str
    office: str
    eta_days: int
    status: Literal["ordered"]


@dataclass(frozen=True, slots=True)
class MockContract:
    description: str
    args: type[_Args]
    result: type[_Result]
    read_only: bool = False


CONTRACTS: dict[str, MockContract] = {
    "create_bitbucket_account": MockContract(
        description=(
            "Create (mock) a Bitbucket account for a new hire in a workspace, with group memberships. "
            "Idempotent on task_id."
        ),
        args=CreateBitbucketAccountArgs,
        result=BitbucketAccount,
    ),
    "provision_laptop": MockContract(
        description="Order (mock) a laptop of a profile for a new hire, shipped to an office. Idempotent on task_id.",
        args=ProvisionLaptopArgs,
        result=LaptopOrder,
    ),
}
TOOL_NAMES: tuple[str, ...] = tuple(sorted(CONTRACTS))
