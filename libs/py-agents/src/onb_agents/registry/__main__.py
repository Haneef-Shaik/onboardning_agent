"""`python -m onb_agents.registry sync --actor <user id> [--config config]`: sync the YAML into the registry.

The MongoDB URI and database come from the environment (`MONGO_URI`, `MONGO_DATABASE`), never from
arguments or code. `--actor` is the HR_ADMIN user id recorded on each audit record. Prints one line per
change; exits 1 with a one-line reason when a manifest is invalid or the sync is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from onb_data import Actor, AuditLog, connect

from .catalog import load_catalog
from .loader import load_manifests
from .manifest import ManifestError
from .mongo import MongoRegistryStore
from .sync import AgentRegistry, RegistrySyncError, SyncReport

__all__ = ["main", "parse_args", "render"]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m onb_agents.registry")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="validate config/agents/*.yaml and sync them into the registry")
    sync.add_argument("--actor", required=True, help="user id of the HR_ADMIN making the change")
    sync.add_argument(
        "--config", type=Path, default=Path("config"), help="directory with agents/ and tool_catalog.yaml"
    )
    return parser.parse_args(argv)


def render(report: SyncReport) -> str:
    lines = [f"{change.action} {change.target}" for change in report.changes]
    orgs = ", ".join(report.audited_orgs) or "none"
    lines.append(f"{len(report.changes)} changed, {len(report.unchanged)} unchanged; audited in: {orgs}")
    return "\n".join(lines)


async def _sync(args: argparse.Namespace, env: Mapping[str, str]) -> SyncReport:
    manifests = load_manifests(args.config / "agents")
    catalog = load_catalog(args.config / "tool_catalog.yaml", manifests)
    uri, database = env.get("MONGO_URI", ""), env.get("MONGO_DATABASE", "")
    if not uri or not database:
        raise RegistrySyncError("MONGO_URI and MONGO_DATABASE are required")
    store = connect(uri, database, app_name="agent-registry-sync")
    try:
        registry = AgentRegistry(MongoRegistryStore(store), AuditLog(store))
        return await registry.sync(manifests, catalog, actor=Actor.user(args.actor))
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = asyncio.run(_sync(args, os.environ if env is None else env))
    except (ManifestError, RegistrySyncError, ValueError) as error:
        sys.stderr.write(f"registry sync refused: {error}\n")
        return 1
    sys.stdout.write(render(report) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
