"""Output contracts `schema_valid` validates against (evals/README.md "Agent output contracts").

`template` is `onb_contracts.templates.Template` (TPL-1). The change request, approval packet and
message are the payload models of the agent output contracts in `onb_contracts.agent_outputs`
(AG-1), which replaced the harness's interim models. Every model forbids unknown fields.
"""

from __future__ import annotations

from onb_contracts.agent_outputs import ApprovalPacket, ChangeRequest, Message
from onb_contracts.templates import Template
from pydantic import BaseModel

__all__ = ["DEFAULT_CONTRACTS", "ApprovalPacket", "ChangeRequest", "Message", "Template"]

DEFAULT_CONTRACTS: dict[str, tuple[str, type[BaseModel]]] = {
    "template": ("template", Template),
    "change_request": ("change_request", ChangeRequest),
    "approval_packet": ("packet", ApprovalPacket),
    "message": ("message", Message),
}
