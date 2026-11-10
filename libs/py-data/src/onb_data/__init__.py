"""Tenant repositories: the only path to MongoDB (PRD §7.9).

    store = connect(settings.mongo_uri, settings.mongo_database)
    with tenant_scope(org_id_from_signed_token):
        hire = await store.tenant("hires").get(hire_id)   # {_id, org_id}; NotFound otherwise

    await store.run_transaction(change_and_event)   # one transaction (EVT-1 outbox)
    store.queue("jobs")                              # workers only: cross-org claim by lease

    audit_log = AuditLog(store)                      # append-only; see `onb_data.audit`
    with tenant_scope(org_id):
        await audit_log.audit(actor=Actor.user(uid), action="pii.reveal", target=t, outcome=AuditOutcome.SUCCESS)

No collection, database or client object is exported.
"""

from .append_only import APPEND_ONLY_COLLECTIONS, AppendOnlyRepository
from .audit import (
    MAX_AUDIT_SKIP,
    Actor,
    ActorType,
    AuditEntry,
    AuditLog,
    Auditor,
    AuditOutcome,
    AuditPage,
    AuditQuery,
    build_record,
    check_page,
)
from .context import TenantContext, current_tenant, tenant_scope
from .errors import (
    DataAccessError,
    InvalidIndexError,
    NoTenantContextError,
    NotFound,
    NotFoundError,
    TenantViolationError,
    TransactionError,
    UnknownCollectionError,
    UnscopedCommandError,
)
from .queue import QUEUE_COLLECTIONS, QueueRepository
from .repository import GlobalRepository, TenantRepository, UpdateOutcome
from .session import in_transaction
from .store import GLOBAL_COLLECTIONS, DataStore, connect

__all__ = [
    "APPEND_ONLY_COLLECTIONS",
    "GLOBAL_COLLECTIONS",
    "MAX_AUDIT_SKIP",
    "PROJECT",
    "QUEUE_COLLECTIONS",
    "Actor",
    "ActorType",
    "AppendOnlyRepository",
    "AuditEntry",
    "AuditLog",
    "AuditOutcome",
    "AuditPage",
    "AuditQuery",
    "Auditor",
    "DataAccessError",
    "DataStore",
    "GlobalRepository",
    "InvalidIndexError",
    "NoTenantContextError",
    "NotFound",
    "NotFoundError",
    "QueueRepository",
    "TenantContext",
    "TenantRepository",
    "TenantViolationError",
    "TransactionError",
    "UnknownCollectionError",
    "UnscopedCommandError",
    "UpdateOutcome",
    "build_record",
    "check_page",
    "connect",
    "current_tenant",
    "in_transaction",
    "tenant_scope",
]

PROJECT = "py-data"
