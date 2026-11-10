"""Tenant repositories: the only path to MongoDB (PRD §7.9).

    store = connect(settings.mongo_uri, settings.mongo_database)
    with tenant_scope(org_id_from_signed_token):
        hire = await store.tenant("hires").get(hire_id)   # {_id, org_id}; NotFound otherwise

    await store.run_transaction(change_and_event)   # one transaction (EVT-1 outbox)
    store.queue("jobs")                              # workers only: cross-org claim by lease

No collection, database or client object is exported.
"""

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
)
from .queue import QUEUE_COLLECTIONS, QueueRepository
from .repository import GlobalRepository, TenantRepository, UpdateOutcome
from .session import in_transaction
from .store import GLOBAL_COLLECTIONS, DataStore, connect

__all__ = [
    "GLOBAL_COLLECTIONS",
    "PROJECT",
    "QUEUE_COLLECTIONS",
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
    "UpdateOutcome",
    "connect",
    "current_tenant",
    "in_transaction",
    "tenant_scope",
]

PROJECT = "py-data"
