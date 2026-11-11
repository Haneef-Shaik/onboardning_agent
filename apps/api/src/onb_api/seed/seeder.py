"""The seed steps: org, org keys, users, agent registry, templates (PRD §7.11; SEED-1).

Seeding is create-only and idempotent. Each step looks first and writes only what is missing, so a
second run changes nothing, and a run that stopped half way finishes the rest:

1. the `orgs` record (global), when absent;
2. the org's keys (PII-1): `keyring.verify` when the org has keys (it unwraps every version, so a
   wrong `PII_MASTER_KEY` stops the seed here), `keyring.create` when it has none;
3. each user, through `UserStore.add_user` (AUTH-1), so the login directory stays in step. An email
   already registered to another user stops the seed; an existing user is left as it is (the seed
   never changes a password or a role);
4. the agent registry, through `AgentRegistry.sync` (REG-1): an unchanged manifest writes nothing;
5. each template through the lifecycle (TPL-2): a draft, then a publish by the seed's HR_ADMIN,
   audited like any publish. A template whose key already has a version is left as it is; an
   unpublished draft of the same body (a run that stopped before its publish) is published, not
   duplicated.

Org, keys, users and templates run inside `tenant_scope(org)`. The publisher must be an HR_ADMIN in
the stored user records too (FR-T4), not only in the seed file.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from onb_agents.registry import AgentRegistry, ToolCatalog
from onb_agents.registry.manifest import AgentManifest
from onb_contracts.auth import Role
from onb_contracts.template_lifecycle import DraftRecord
from onb_contracts.templates import Template
from onb_crypto import Keyring, NoOrgKeysError
from onb_data import Actor, tenant_scope
from pymongo.errors import DuplicateKeyError

from onb_api.auth.passwords import hash_password
from onb_api.auth.users import NewUser, UserStore
from onb_api.seed.spec import SeedPlan, SeedUser
from onb_api.templates import TemplateLifecycle

__all__ = ["OrgRecords", "SeedConflictError", "SeedReport", "SeedServices", "SeedStep", "Seeder"]

_LIFECYCLE_FIELDS: Final = frozenset({"version", "status"})


class SeedConflictError(RuntimeError):
    """The database holds something the seed cannot reconcile without changing it."""


@dataclass(frozen=True, slots=True)
class SeedStep:
    created: bool
    target: str

    def line(self) -> str:
        return f"{'created' if self.created else 'unchanged'} {self.target}"


@dataclass(frozen=True, slots=True)
class SeedReport:
    steps: tuple[SeedStep, ...]

    @property
    def created(self) -> tuple[SeedStep, ...]:
        return tuple(s for s in self.steps if s.created)

    @property
    def unchanged(self) -> tuple[SeedStep, ...]:
        return tuple(s for s in self.steps if not s.created)

    def render(self) -> str:
        summary = f"{len(self.created)} created, {len(self.unchanged)} unchanged"
        return "\n".join([*(s.line() for s in self.steps), summary])


class OrgRecords(Protocol):
    """The global `orgs` collection as the seed uses it (`store.shared("orgs")`)."""

    async def find_one(self, query: Mapping[str, Any] | None = None) -> dict[str, Any] | None: ...

    async def insert_one(self, document: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class SeedServices:
    orgs: OrgRecords
    keyring: Keyring
    users: UserStore
    registry: AgentRegistry
    templates: TemplateLifecycle
    manifests: Sequence[AgentManifest]
    catalog: ToolCatalog
    indexes: Sequence[Callable[[], Awaitable[object]]] = field(default=())


class Seeder:
    def __init__(self, services: SeedServices, *, bcrypt_rounds: int) -> None:
        self._s = services
        self._rounds = bcrypt_rounds

    async def run(self, plan: SeedPlan) -> SeedReport:
        for ensure in self._s.indexes:
            await ensure()
        steps: list[SeedStep] = [await self._org(plan)]
        org_id = plan.org.id
        with tenant_scope(org_id):
            steps.append(await self._keys(org_id))
            steps.extend([await self._user(org_id, user) for user in plan.users])
            await self._check_publisher(plan.actor)
        steps.extend(await self._registry(plan.actor))
        with tenant_scope(org_id):
            steps.extend([await self._template(t, plan.actor) for t in plan.templates])
        return SeedReport(steps=tuple(steps))

    async def _org(self, plan: SeedPlan) -> SeedStep:
        target = f"org:{plan.org.id}"
        if await self._s.orgs.find_one({"_id": plan.org.id}) is not None:
            return SeedStep(created=False, target=target)
        try:
            await self._s.orgs.insert_one({"_id": plan.org.id, "name": plan.org.name})
        except DuplicateKeyError:  # another seed got there first: the same end state
            return SeedStep(created=False, target=target)
        return SeedStep(created=True, target=target)

    async def _keys(self, org_id: str) -> SeedStep:
        try:
            version = await self._s.keyring.verify(org_id)
        except NoOrgKeysError:
            version = await self._s.keyring.create(org_id)
            return SeedStep(created=True, target=f"org_keys:{org_id}/v{version}")
        return SeedStep(created=False, target=f"org_keys:{org_id}/v{version}")

    async def _user(self, org_id: str, user: SeedUser) -> SeedStep:
        target = f"user:{user.id} ({user.role.value})"
        entry = await self._s.users.find_login(user.email)
        if entry is not None:
            if (entry.org_id, entry.user_id) != (org_id, user.id):
                raise SeedConflictError(f"the email of {user.id} is registered to another user")
            if await self._s.users.get_user(user.id) is None:
                raise SeedConflictError(f"{user.id} has a login entry but no user record")
            return SeedStep(created=False, target=target)
        password_hash = await asyncio.to_thread(hash_password, user.password, rounds=self._rounds)
        new = NewUser(email=user.email, name=user.name, role=user.role, password_hash=password_hash)
        await self._s.users.add_user(new, user_id=user.id)
        return SeedStep(created=True, target=target)

    async def _check_publisher(self, actor: SeedUser) -> None:
        stored = await self._s.users.get_user(actor.id)
        if stored is None or stored.role is not Role.HR_ADMIN:
            raise SeedConflictError(f"{actor.id} is not an HR_ADMIN: only an HR_ADMIN publishes templates")

    async def _registry(self, actor: SeedUser) -> list[SeedStep]:
        report = await self._s.registry.sync(self._s.manifests, self._s.catalog, actor=Actor.user(actor.id))
        created = [SeedStep(created=True, target=change.target) for change in report.changes]
        return created + [SeedStep(created=False, target=f"agent:{name}") for name in report.unchanged]

    async def _template(self, template: Template, actor: SeedUser) -> SeedStep:
        current = next((t for t in await self._s.templates.list_templates() if t.key == template.key), None)
        if current is not None:
            return SeedStep(created=False, target=f"template:{template.key}/v{current.latest_version}")
        draft = await self._pending_draft(template)
        if draft is None:
            draft = await self._s.templates.create_draft(template, actor_id=actor.id)
        published = await self._s.templates.publish(draft.id, actor_id=actor.id)
        return SeedStep(created=True, target=f"template:{published.key}/v{published.version}")

    async def _pending_draft(self, template: Template) -> DraftRecord | None:
        """An unpublished draft holding exactly this body: left by a run that stopped before publishing."""
        wanted = _body(template)
        drafts = await self._s.templates.list_drafts()
        return next((d for d in drafts if d.status == "draft" and _body(d.template) == wanted), None)


def _body(template: Template) -> dict[str, Any]:
    return template.model_dump(mode="json", exclude=set(_LIFECYCLE_FIELDS))
