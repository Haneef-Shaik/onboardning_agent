"""`UserStore` over `onb_data`: the global `user_logins` directory and the tenant `users` collection.

Every read and write goes through the data layer's repositories (PRD §7.9 Data access). The
directory's `_id` is unique, so it also makes an email unique across orgs. A user is added
directory-first; if the user insert then fails, the directory entry is removed again.
"""

from __future__ import annotations

import secrets
from typing import Any

from onb_contracts.auth import Role
from onb_data import DataStore, NotFoundError, current_tenant
from pymongo.errors import DuplicateKeyError

from onb_api.auth.users import EmailTakenError, LoginEntry, NewUser, UserRecord, login_key, normalise_email

__all__ = ["LOGINS", "USERS", "MongoUserStore"]

LOGINS = "user_logins"
USERS = "users"


class MongoUserStore:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(USERS, [("org_id", 1), ("email", 1)], unique=True, name="org_email")

    async def find_login(self, email: str) -> LoginEntry | None:
        entry = await self._store.shared(LOGINS).find_one({"_id": login_key(email)})
        if entry is None:
            return None
        return LoginEntry(org_id=entry["org_id"], user_id=entry["user_id"])

    async def get_user(self, user_id: str) -> UserRecord | None:
        try:
            doc = await self._store.tenant(USERS).get(user_id)
        except NotFoundError:
            return None
        return _record(doc)

    async def add_user(self, user: NewUser, *, user_id: str | None = None) -> UserRecord:
        org_id = current_tenant().org_id
        new_id = user_id or f"u_{secrets.token_hex(12)}"
        key = login_key(user.email)
        logins = self._store.shared(LOGINS)
        try:
            await logins.insert_one({"_id": key, "org_id": org_id, "user_id": new_id})
        except DuplicateKeyError:
            raise EmailTakenError("email already registered") from None
        doc = {
            "_id": new_id,
            "email": normalise_email(user.email),
            "name": user.name,
            "role": user.role.value,
            "password_hash": user.password_hash,
        }
        try:
            await self._store.tenant(USERS).insert_one(doc)
        except BaseException:
            await logins.delete_by_id(key)
            raise
        return _record({**doc, "org_id": org_id})


def _record(doc: dict[str, Any]) -> UserRecord:
    return UserRecord(
        user_id=doc["_id"],
        org_id=doc["org_id"],
        email=doc["email"],
        name=doc["name"],
        role=Role(doc["role"]),
        password_hash=doc["password_hash"],
    )
