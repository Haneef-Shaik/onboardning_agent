"""The agent registry (PRD §5.2, §7.3, FR-A1): validated manifests, versioned and audited.

    manifests = load_manifests(Path("config/agents"))           # one YAML per agent
    catalog = load_catalog(Path("config/tool_catalog.yaml"), manifests)
    registry = AgentRegistry(MongoRegistryStore(store), AuditLog(store))
    report = await registry.sync(manifests, catalog, actor=Actor.user(admin_id))

`python -m onb_agents.registry sync --actor <user id>` does the same from the command line.
"""

from .catalog import CatalogEntry, ToolCatalog, load_catalog, parse_catalog
from .loader import load_manifests
from .manifest import AgentManifest, ManifestError, parse_manifest
from .sync import AgentRegistry, RegisteredAgent, RegistryStore, RegistrySyncError, SyncChange, SyncReport

__all__ = [
    "AgentManifest",
    "AgentRegistry",
    "CatalogEntry",
    "ManifestError",
    "RegisteredAgent",
    "RegistryStore",
    "RegistrySyncError",
    "SyncChange",
    "SyncReport",
    "ToolCatalog",
    "load_catalog",
    "load_manifests",
    "parse_catalog",
    "parse_manifest",
]
