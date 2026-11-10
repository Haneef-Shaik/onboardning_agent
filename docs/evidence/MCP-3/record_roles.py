"""Record the MCP database users' role definitions (MCP-3, Done-when box 3).

Starts a throw-away MongoDB from the compose image, provisions the users with generated passwords
(never printed), then prints `db.getUser(..., {showPrivileges: true})` and `db.getRole(...)` from
mongosh inside the container. Run from the repo root:

    uv run --frozen python docs/evidence/MCP-3/record_roles.py > docs/evidence/MCP-3/db-getUser.txt
"""

from __future__ import annotations

import secrets
import sys
import time
from typing import Any

from onb_mcp_mongo.db_users import READ_ONLY, READ_WRITE, provision
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from testcontainers.community.mongodb import MongoDbContainer

MONGO_IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
DATABASE = "onboarding"


def _wait(client: MongoClient[dict[str, Any]]) -> None:
    end = time.monotonic() + 60
    while True:
        try:
            client.admin.command("ping")
            return
        except PyMongoError:
            if time.monotonic() > end:
                raise
            time.sleep(0.5)


def main() -> None:
    root_password = secrets.token_hex(16)
    with MongoDbContainer(MONGO_IMAGE, username="root", password=root_password) as container:
        client: MongoClient[dict[str, Any]] = MongoClient(container.get_connection_url())
        try:
            _wait(client)
            provision(
                client["admin"], DATABASE, read_password=secrets.token_hex(16), write_password=secrets.token_hex(16)
            )
        finally:
            client.close()
        script = "; ".join(
            f"print('// db.getUser(\"{s.user}\", {{showPrivileges: true}})'); "
            f"printjson(db.getUser('{s.user}', {{showPrivileges: true}})); "
            f"print('// db.getRole(\"{s.role}\", {{showPrivileges: true}})'); "
            f"printjson(db.getRole('{s.role}', {{showPrivileges: true}}))"
            for s in (READ_ONLY, READ_WRITE)
        )
        code, output = container.exec(
            [
                "mongosh",
                "--quiet",
                "-u",
                "root",
                "-p",
                root_password,
                "--authenticationDatabase",
                "admin",
                "admin",
                "--eval",
                script,
            ]
        )
    if code != 0:
        sys.exit(f"mongosh exited {code}")
    sys.stdout.write(f"# MCP-3 role definitions; mongo image {MONGO_IMAGE}; application database {DATABASE}\n")
    sys.stdout.write(output.decode().replace(root_password, "<redacted>"))


if __name__ == "__main__":
    main()
