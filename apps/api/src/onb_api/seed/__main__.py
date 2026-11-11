"""`python -m onb_api.seed [--seed config/seed.yaml] [--config config]`: what `make seed` runs (PRD §7.11).

Configuration comes from the environment only: `MONGO_URI`, `MONGO_DATABASE`, `PII_MASTER_KEY` and the
`SEED_*_PASSWORD` variables `config/seed.yaml` names. Prints one line per record (`created …` or
`unchanged …`) and a summary; exits 1 with a one-line reason, which never holds a password, a key or
the Mongo URI, when the seed is refused or fails.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from onb_agents.registry import AgentRegistry, RegistrySyncError, load_catalog, load_manifests
from onb_agents.registry.manifest import ManifestError
from onb_agents.registry.mongo import MongoRegistryStore
from onb_crypto import (
    ORG_KEYS_COLLECTION,
    CryptoError,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    ensure_org_keys_index,
)
from onb_data import AuditLog, DataAccessError, DataStore, connect
from pymongo.errors import PyMongoError

from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.settings import DEFAULT_BCRYPT_ROUNDS, ConfigError, require_env
from onb_api.auth.users import EmailTakenError
from onb_api.seed.seeder import SeedConflictError, Seeder, SeedReport, SeedServices
from onb_api.seed.spec import SeedConfigError, load_seed
from onb_api.templates import (
    LifecycleConflictError,
    MongoTemplateStore,
    RegistryValidatorSource,
    TemplateLifecycle,
    TemplateRejectedError,
)

__all__ = ["build_services", "main", "parse_args"]

_REFUSALS = (
    SeedConfigError,
    SeedConflictError,
    ConfigError,
    CryptoError,
    RegistrySyncError,
    ManifestError,
    EmailTakenError,
    LifecycleConflictError,  # another seed published the same template at the same time: run it again
    DataAccessError,
)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m onb_api.seed", description="Seed the org, users and templates.")
    parser.add_argument("--seed", type=Path, default=Path("config/seed.yaml"), help="the seed file")
    parser.add_argument(
        "--config", type=Path, default=Path("config"), help="directory with agents/, tool_catalog.yaml and the policy"
    )
    return parser.parse_args(argv)


def build_services(store: DataStore, master_key: MasterKey, config_dir: Path) -> SeedServices:
    """The production services over `store`: the same classes `onb_api.main` wires."""
    audit_log = AuditLog(store)
    users = MongoUserStore(store)
    registry = AgentRegistry(MongoRegistryStore(store), audit_log)
    templates = TemplateLifecycle(
        MongoTemplateStore(store), RegistryValidatorSource.from_config(registry, config_dir), audit_log
    )
    manifests = load_manifests(config_dir / "agents")

    async def org_keys_index() -> None:
        await ensure_org_keys_index(store)

    return SeedServices(
        orgs=store.shared("orgs"),
        keyring=Keyring(master_key, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION))),
        users=users,
        registry=registry,
        templates=templates,
        manifests=manifests,
        catalog=load_catalog(config_dir / "tool_catalog.yaml", manifests),
        indexes=(org_keys_index, users.ensure_indexes, audit_log.ensure_indexes, templates.ensure_indexes),
    )


async def _seed(args: argparse.Namespace, env: Mapping[str, str]) -> SeedReport:
    plan = load_seed(args.seed, env)
    master_key = MasterKey.from_env(env)
    uri, database = require_env(env, "MONGO_URI"), require_env(env, "MONGO_DATABASE")
    store = connect(uri, database, app_name="seed")
    try:
        seeder = Seeder(build_services(store, master_key, args.config), bcrypt_rounds=DEFAULT_BCRYPT_ROUNDS)
        return await seeder.run(plan)
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = asyncio.run(_seed(args, os.environ if env is None else env))
    except TemplateRejectedError as error:
        found = ", ".join(sorted({f"{i.rule.value}@{i.task_id or '-'}" for i in error.issues}))
        sys.stderr.write(f"seed refused: a seed template is invalid ({found})\n")
        return 1
    except _REFUSALS as error:
        sys.stderr.write(f"seed refused: {error}\n")
        return 1
    except PyMongoError as error:  # its text can carry the host; the type is enough to act on
        sys.stderr.write(f"seed failed: MongoDB error {type(error).__name__}\n")
        return 1
    sys.stdout.write(report.render() + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
