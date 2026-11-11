"""The seed file (`config/seed.yaml`) and its passwords (PRD §7.11; SEED-1).

The file lists the org, its users and the template files to publish. A user names the environment
variable holding its password (`password_env`, a `SEED_*_PASSWORD` name); the file itself can hold no
password (unknown fields are refused). `load_seed` reads those variables and fails with a message
naming the variable, never its value, when one is missing, shorter than `MIN_PASSWORD_LENGTH`, or
longer than bcrypt reads. Passwords are kept out of every repr.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml
from onb_contracts.auth import MAX_EMAIL_LENGTH, Role
from onb_contracts.templates import Template
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from onb_api.auth.passwords import MAX_PASSWORD_BYTES
from onb_api.auth.users import normalise_email

__all__ = ["MIN_PASSWORD_LENGTH", "SeedConfigError", "SeedOrg", "SeedPlan", "SeedUser", "load_seed"]

MIN_PASSWORD_LENGTH: Final = 12
_ORG_ID: Final = r"^org_[a-z0-9_]{1,60}$"
_USER_ID: Final = r"^u_[a-z0-9_]{1,60}$"
_ENV_NAME: Final = r"^SEED_[A-Z0-9_]{1,60}_PASSWORD$"


class SeedConfigError(ValueError):
    """The seed file or its environment is unusable; messages name variables and fields, never values."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class _OrgEntry(_Strict):
    id: str = Field(pattern=_ORG_ID)
    name: str = Field(min_length=1, max_length=200)


class _UserEntry(_Strict):
    id: str = Field(pattern=_USER_ID)
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    role: Role
    password_env: str = Field(pattern=_ENV_NAME)


class _SeedFile(_Strict):
    org: _OrgEntry
    users: list[_UserEntry] = Field(min_length=1, max_length=100)
    templates: list[str] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True, slots=True)
class SeedOrg:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class SeedUser:
    id: str
    email: str
    name: str
    role: Role
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SeedPlan:
    org: SeedOrg
    users: tuple[SeedUser, ...]
    templates: tuple[Template, ...]

    @property
    def actor(self) -> SeedUser:
        """The first HR_ADMIN: who syncs the registry and publishes the templates (FR-T4)."""
        return next(u for u in self.users if u.role is Role.HR_ADMIN)


def load_seed(path: Path, env: Mapping[str, str]) -> SeedPlan:
    entries = _parse(path)
    _check_users(entries.users)
    users = tuple(_user(entry, env) for entry in entries.users)
    templates = tuple(_template(path.parent / ref) for ref in entries.templates)
    _check_unique("template key", [t.key for t in templates])
    return SeedPlan(org=SeedOrg(id=entries.org.id, name=entries.org.name), users=users, templates=templates)


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SeedConfigError(f"{path} not found") from None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise SeedConfigError(f"{path} is not readable YAML: {type(error).__name__}") from None


def _fields(error: ValidationError) -> str:
    """Where the file is wrong, without the offending values (one may be a password)."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors(include_input=False))


def _parse(path: Path) -> _SeedFile:
    try:
        return _SeedFile.model_validate(_read_yaml(path))
    except ValidationError as error:
        raise SeedConfigError(f"{path.name}: {_fields(error)}") from None


def _check_unique(what: str, values: list[str]) -> None:
    repeated = sorted(v for v, n in Counter(values).items() if n > 1)
    if repeated:
        raise SeedConfigError(f"{what} repeated: {', '.join(repeated)}")


def _check_users(users: list[_UserEntry]) -> None:
    _check_unique("user id", [u.id for u in users])
    _check_unique("user email", [normalise_email(u.email) for u in users])
    present = {u.role for u in users}
    missing = [role.value for role in Role if role not in present]
    if missing:
        raise SeedConfigError(f"the seed needs at least one user with role {', '.join(missing)}")


def _password(name: str, env: Mapping[str, str]) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise SeedConfigError(f"{name} is not set")
    if value != value.strip():
        # Stored as given, a login typed without the spaces would fail; stripped, one typed with them would.
        raise SeedConfigError(f"{name} must not start or end with whitespace")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise SeedConfigError(f"{name} must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value.encode()) > MAX_PASSWORD_BYTES:
        raise SeedConfigError(f"{name} must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


def _user(entry: _UserEntry, env: Mapping[str, str]) -> SeedUser:
    password = _password(entry.password_env, env)
    return SeedUser(id=entry.id, email=entry.email, name=entry.name, role=entry.role, password=password)


def _template(path: Path) -> Template:
    try:
        return Template.model_validate(_read_yaml(path))
    except ValidationError as error:
        raise SeedConfigError(f"{path.name} is not a template: {_fields(error)}") from None
