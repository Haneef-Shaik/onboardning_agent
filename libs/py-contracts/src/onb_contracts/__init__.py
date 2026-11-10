"""Shared Pydantic contracts: the response envelope, error codes, page meta and request bases (PRD §10)."""

from onb_contracts.envelope import (
    MAX_PAGE_LIMIT,
    Envelope,
    ErrorBody,
    ErrorDetail,
    ErrorEnvelope,
    Meta,
    PageMeta,
    fail,
    ok,
)
from onb_contracts.errors import ERROR_STATUS, ErrorCode, code_for_status
from onb_contracts.inputs import (
    RequestModel,
    is_tenant_key,
    request_model_classes,
    tenant_inputs_in_model,
    tenant_inputs_in_type,
)

__all__ = [
    "ERROR_STATUS",
    "MAX_PAGE_LIMIT",
    "PROJECT",
    "Envelope",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "ErrorEnvelope",
    "Meta",
    "PageMeta",
    "RequestModel",
    "code_for_status",
    "fail",
    "is_tenant_key",
    "ok",
    "request_model_classes",
    "tenant_inputs_in_model",
    "tenant_inputs_in_type",
]

PROJECT = "py-contracts"
