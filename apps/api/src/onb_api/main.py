"""ASGI entry point: `uvicorn onb_api.main:app`.

Telemetry starts first, so the FastAPI instrumentation reaches the app (F-4: the instrumentor swaps
the `FastAPI` class). Export settings come from the standard `OTEL_*` environment.

Startup fails (`ConfigError`) when `JWT_SECRET`, `MONGO_URI`, `MONGO_DATABASE` or `CONFIG_DIR` is
missing, and (`MasterKeyError`) when `PII_MASTER_KEY` is missing or not base64 of 32 bytes (PRD §7.8,
§7.10). `CONFIG_DIR` is the repository's `config/` (the tool catalog and the field → sink policy the
template validator reads, TPL-2).
"""

import os
from pathlib import Path

from onb_agents.registry import AgentRegistry
from onb_agents.registry.mongo import MongoRegistryStore
from onb_crypto import ORG_KEYS_COLLECTION, Keyring, MasterKey, RepositoryOrgKeyStore
from onb_data import AuditLog, connect
from onb_telemetry import init_telemetry

from onb_api.app import create_app
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.settings import AuthSettings, require_env
from onb_api.documents.service import DocumentService
from onb_api.health import SERVICE_NAME
from onb_api.hires.service import HireService
from onb_api.templates import MongoTemplateStore, RegistryValidatorSource, TemplateLifecycle

auth = AuthSettings.from_env()
mongo_uri = require_env(os.environ, "MONGO_URI")
mongo_database = require_env(os.environ, "MONGO_DATABASE")
master_key = MasterKey.from_env()
config_dir = Path(require_env(os.environ, "CONFIG_DIR"))
# Telemetry before the Mongo client: PyMongo applies the tracing listener only to clients made after it.
telemetry = init_telemetry(SERVICE_NAME)
store = connect(mongo_uri, mongo_database)
audit_log = AuditLog(store)
users = MongoUserStore(store)
registry = AgentRegistry(MongoRegistryStore(store), audit_log)
keyring = Keyring(master_key, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
hires = HireService.build(store, keyring, auditor=audit_log, users=users)
templates = TemplateLifecycle(
    MongoTemplateStore(store), RegistryValidatorSource.from_config(registry, config_dir), audit_log
)
documents = DocumentService.build(store, keyring, auditor=audit_log)
app = create_app(
    auth=auth,
    users=users,
    audit=audit_log,
    agents=registry,
    hires=hires,
    templates=templates,
    documents=documents,
)
