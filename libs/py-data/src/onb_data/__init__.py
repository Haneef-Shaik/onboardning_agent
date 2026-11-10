"""Tenant repositories: the only path to MongoDB (PRD §7.9).

    store = connect(settings.mongo_uri, settings.mongo_database)
    with tenant_scope(org_id_from_signed_token):
        hire = await store.tenant("hires").get(hire_id)   # {_id, org_id}; NotFound otherwise

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
    UnknownCollectionError,
)
from .repository import GlobalRepository, TenantRepository, UpdateOutcome
from .store import GLOBAL_COLLECTIONS, DataStore, connect

__all__ = [
    "GLOBAL_COLLECTIONS",
    "PROJECT",
    "DataAccessError",
    "DataStore",
    "GlobalRepository",
    "InvalidIndexError",
    "NoTenantContextError",
    "NotFound",
    "NotFoundError",
    "TenantContext",
    "TenantRepository",
    "TenantViolationError",
    "UnknownCollectionError",
    "UpdateOutcome",
    "connect",
    "current_tenant",
    "tenant_scope",
]

PROJECT = "py-data"
