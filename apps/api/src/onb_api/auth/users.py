"""Users and the login directory (PRD §3, §7.8, §9 `users`; ADR 0004).

A login names an email, not an org, so the user's org has to be found before any tenant scope
exists. The `user_logins` directory is a global collection holding only `{_id: login_key(email),
org_id, user_id}`. The user record itself (name, email, role, password hash) is a tenant document in
`users`, read inside the org's scope like any other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from onb_contracts.auth import Role

__all__ = ["EmailTakenError", "LoginEntry", "NewUser", "UserRecord", "UserStore", "login_key", "normalise_email"]


class EmailTakenError(Exception):
    """An email can belong to one user only, across every org."""


def normalise_email(email: str) -> str:
    return email.strip().casefold()


def login_key(email: str) -> str:
    """The directory key: SHA-256 of the normalised email, so the global directory holds no address."""
    return hashlib.sha256(normalise_email(email).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LoginEntry:
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class NewUser:
    email: str
    name: str
    role: Role
    password_hash: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    org_id: str
    email: str
    name: str
    role: Role
    password_hash: str = field(repr=False)


class UserStore(Protocol):
    async def find_login(self, email: str) -> LoginEntry | None:
        """The directory entry for this email, from any org; `None` if there is none."""

    async def get_user(self, user_id: str) -> UserRecord | None:
        """The user in the current tenant scope; `None` if there is none (or it belongs to another org)."""

    async def add_user(self, user: NewUser, *, user_id: str | None = None) -> UserRecord:
        """Create a user in the current tenant scope; `EmailTakenError` if the email is registered."""
