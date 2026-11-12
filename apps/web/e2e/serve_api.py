"""The real API for the Playwright smoke test (UI-1), on 127.0.0.1 only.

AUTH-1's app (`create_app`: login, JWT, `/me`, the envelope) with its in-memory user store, seeded
with the synthetic users of `onb_api.testing.SEED_USERS`. No Mongo: the Mongo-backed stores are proved
by their own integration tests, and the compose golden path is E2E-1's.

UI-8: the admin routes' services, seeded in memory through the real services (`admin_seed.py`).
UI-5: runs are seeded by `run_seed.py` and a test router (`/e2e/...`, 127.0.0.1 only) plays the orchestrator.
UI-4: that seed also builds the real document service next to the hire service (vault, masking, audited
reveal, encrypted scans, one master key per run), which the hires screens use.

Test-only. The password and the JWT secret come from the environment (Playwright makes fresh ones
per run); nothing here is a real credential.
"""

from __future__ import annotations

import asyncio
import os
import sys

import uvicorn
from admin_seed import admin_services
from onb_api.app import create_app
from onb_api.auth.passwords import hash_password
from onb_api.auth.settings import AuthSettings, require_env
from onb_api.auth.users import NewUser
from onb_api.testing import SEED_USERS, InMemoryUserStore, email_of
from onb_data import tenant_scope
from run_seed import driver_router

HOST = "127.0.0.1"
BCRYPT_ROUNDS = 4  # test speed; the production default stays 12


async def seeded(password: str) -> InMemoryUserStore:
    store = InMemoryUserStore()
    password_hash = hash_password(password, rounds=BCRYPT_ROUNDS)
    for user_id, org_id, name, role in SEED_USERS:
        with tenant_scope(org_id):
            user = NewUser(email=email_of(user_id), name=name, role=role, password_hash=password_hash)
            await store.add_user(user, user_id=user_id)
    return store


def main() -> int:
    port = int(require_env(os.environ, "E2E_API_PORT"))
    password = require_env(os.environ, "E2E_PASSWORD")
    settings = AuthSettings(jwt_secret=require_env(os.environ, "E2E_JWT_SECRET"), bcrypt_rounds=BCRYPT_ROUNDS)
    users = asyncio.run(seeded(password))
    admin = asyncio.run(admin_services(users))  # includes the hire and document services (UI-4)
    driver = admin.pop("run_driver")  # UI-5: the test's stand-in for the orchestrator
    app = create_app(auth=settings, users=users, chat=driver.chat, **admin)
    app.include_router(driver_router(driver))
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
