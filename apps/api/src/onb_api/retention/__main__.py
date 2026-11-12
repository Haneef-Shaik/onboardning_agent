"""`python -m onb_api.retention [--once]`: the PII retention purge as its own process (PII-5, PRD §7.10).

Configuration comes from the environment only: `MONGO_URI`, `MONGO_DATABASE`, `PII_MASTER_KEY`, and
optionally `PII_PURGE_INTERVAL_SECONDS` (default 3600: how often the job looks for hires whose 90 days are
up). `--once` runs a single pass and prints its counts (for an operator or a cron). Otherwise it runs until
SIGINT/SIGTERM. Needs a Mongo user that may update the vault collections and `new_hires`, read
`onboarding_runs` and `orgs`, and insert into `audit_log` (deploy note in the PII-5 handoff).
Nothing it prints holds a value, a key or the Mongo URI.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from typing import Final

from onb_crypto import (
    ORG_KEYS_COLLECTION,
    CryptoError,
    Keyring,
    MasterKey,
    RepositoryOrgKeyStore,
    Vault,
)
from onb_data import AuditLog, DataAccessError, connect
from pymongo.errors import PyMongoError

from onb_api.auth.settings import ConfigError, require_env
from onb_api.retention.job import PiiRetentionJob, PurgeReport
from onb_api.retention.loop import run_forever

__all__ = ["DEFAULT_INTERVAL_SECONDS", "interval_from", "main", "parse_args"]

DEFAULT_INTERVAL_SECONDS: Final = 3600.0
_MIN_INTERVAL_SECONDS: Final = 1.0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m onb_api.retention", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    return parser.parse_args(argv)


def interval_from(env: Mapping[str, str]) -> float:
    raw = env.get("PII_PURGE_INTERVAL_SECONDS")
    if raw is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError("PII_PURGE_INTERVAL_SECONDS must be a number") from None
    if not math.isfinite(value) or value < _MIN_INTERVAL_SECONDS:
        raise ConfigError(f"PII_PURGE_INTERVAL_SECONDS must be a number of at least {_MIN_INTERVAL_SECONDS:g}")
    return value


def _summary(report: PurgeReport) -> str:
    return f"hires purged: {report.hires_purged}, values purged: {report.values_purged}, failures: {report.failures}"


async def _serve(env: Mapping[str, str], *, once: bool) -> int:
    mongo_uri = require_env(env, "MONGO_URI")
    mongo_database = require_env(env, "MONGO_DATABASE")
    master_key = MasterKey.from_env(env)
    interval = interval_from(env)
    store = connect(mongo_uri, mongo_database)
    keyring = Keyring(master_key, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    job = PiiRetentionJob(store, Vault(keyring, store), AuditLog(store))
    if once:
        report = await job.run_once()
        print(_summary(report))
        return 1 if report.failures else 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    print(f"pii retention purge running every {interval:g}s", flush=True)
    for report in await run_forever(job, interval=interval, stop=stop):
        if report.hires_purged or report.failures:
            print(_summary(report), flush=True)
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return asyncio.run(_serve(os.environ if env is None else env, once=args.once))
    except (ConfigError, CryptoError, DataAccessError, PyMongoError) as error:
        print(f"pii retention purge stopped: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
