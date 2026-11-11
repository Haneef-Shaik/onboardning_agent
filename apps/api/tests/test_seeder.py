"""The seed steps below the database driver (SEED-1 boxes 1 and 2; PRD §7.11).

The seeder runs over the in-memory database (`memory_store`, so `orgs`, `org_keys`, `users` and
`user_logins` go through the production repositories, `MongoUserStore` and the keyring), the in-memory
agent registry and template store. `tests_int/test_mongo_seed.py` runs the same against MongoDB.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from onb_agents.registry import AgentRegistry, load_catalog, load_manifests
from onb_agents.testing import InMemoryRegistryStore
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.passwords import hash_password, verify_password
from onb_api.auth.users import NewUser
from onb_api.seed.seeder import SeedConflictError, Seeder, SeedServices
from onb_api.seed.spec import SeedPlan, SeedUser, load_seed
from onb_api.templates import RegistryValidatorSource, TemplateLifecycle
from onb_api.testing import InMemoryAuditLog, InMemoryTemplateStore
from onb_api.testing_mongo import MemoryDatabase, memory_keyring, memory_store
from onb_contracts.auth import Role
from onb_crypto import Keyring, KeyUnwrapError
from onb_data import Actor, AuditOutcome, AuditQuery, DataStore, tenant_scope

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config"
ENV = {
    "SEED_HR_ADMIN_PASSWORD": "admin-pass-0123456789",
    "SEED_HR_PASSWORD": "hr-pass-0123456789",
    "SEED_MANAGER_PASSWORD": "manager-pass-0123456789",
}
ROUNDS = 4
ORG = "org_demo"
# A hash of a throwaway value, for users the seed did not make.
OTHER_HASH = hash_password("not-a-seed-password", rounds=ROUNDS)


class OrgsRegistryStore(InMemoryRegistryStore):
    """The in-memory registry, auditing in the orgs the seed stored (as `MongoRegistryStore` does)."""

    def __init__(self, store: DataStore) -> None:
        super().__init__(org_ids=())
        self._store = store

    async def org_ids(self) -> list[str]:
        return [str(o["_id"]) for o in await self._store.shared("orgs").find({}, projection={"_id": 1})]


@dataclass
class World:
    db: MemoryDatabase
    store: DataStore
    keyring: Keyring
    users: MongoUserStore
    registry_store: OrgsRegistryStore
    registry: AgentRegistry
    template_store: InMemoryTemplateStore
    templates: TemplateLifecycle
    audit: InMemoryAuditLog

    def services(self) -> SeedServices:
        manifests = load_manifests(CONFIG / "agents")
        return SeedServices(
            orgs=self.store.shared("orgs"),
            keyring=self.keyring,
            users=self.users,
            registry=self.registry,
            templates=self.templates,
            manifests=manifests,
            catalog=load_catalog(CONFIG / "tool_catalog.yaml", manifests),
            indexes=(self.users.ensure_indexes,),
        )

    def seeder(self) -> Seeder:
        return Seeder(self.services(), bcrypt_rounds=ROUNDS)


def _world(audit: InMemoryAuditLog | None = None) -> World:
    db = MemoryDatabase()
    store = memory_store(db)
    log = audit or InMemoryAuditLog()
    registry_store = OrgsRegistryStore(store)
    registry = AgentRegistry(registry_store, log)
    template_store = InMemoryTemplateStore()
    templates = TemplateLifecycle(template_store, RegistryValidatorSource.from_config(registry, CONFIG), log)
    keyring, users = memory_keyring(store), MongoUserStore(store)
    return World(db, store, keyring, users, registry_store, registry, template_store, templates, log)


@pytest.fixture
def plan() -> SeedPlan:
    return load_seed(CONFIG / "seed.yaml", ENV)


def _run[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(make())


def _in_org[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    async def scoped() -> T:
        with tenant_scope(ORG):
            return await make()

    return asyncio.run(scoped())


def _snapshot(world: World) -> dict[str, Any]:
    """Everything the seed can write: the database, the registry, the template store and the audit log."""

    async def audit_entries() -> list[Any]:
        with tenant_scope(ORG):
            page = await world.audit.list(AuditQuery(), page=1, limit=200)
        return list(page.entries)

    return copy.deepcopy(
        {
            "db": {name: world.db.raw(name) for name in sorted(world.db.collections)},
            "agents": _run(world.registry_store.agents),
            "catalog": _run(world.registry_store.catalog),
            "templates": vars(world.template_store),
            "audit": _run(audit_entries),
        }
    )


# --- box 1: an empty database gets every record ------------------------------------------------------


def test_an_empty_database_gets_the_org_its_keys_the_users_the_registry_and_the_template(plan: SeedPlan) -> None:
    world = _world()
    report = _run(lambda: world.seeder().run(plan))

    assert world.db.raw("orgs") == [{"_id": ORG, "name": "Demo Org (synthetic)"}]
    assert _in_org(lambda: world.keyring.verify(ORG)) == 1
    stored = {u["_id"]: u["role"] for u in world.db.raw("users")}
    assert stored == {
        "u_demo_hradmin": "HR_ADMIN",
        "u_demo_hr": "HR",
        "u_demo_mgr_priya": "MANAGER",
        "u_demo_mgr_arjun": "MANAGER",
    }
    assert {d["user_id"] for d in world.db.raw("user_logins")} == set(stored)
    agents = _run(world.registry.list_agents)
    assert [a.name for a in agents] == sorted(m.name for m in load_manifests(CONFIG / "agents"))
    assert all(a.active and a.version == 1 for a in agents)
    assert _run(world.registry_store.catalog) is not None

    summaries = _in_org(world.templates.list_templates)
    assert [(t.key, t.latest_version) for t in summaries] == [("engineer-india", 1)]
    version = _in_org(lambda: world.templates.get_version("engineer-india", 1))
    assert version.status == "published"
    assert version.published_by == "u_demo_hradmin"

    assert [s.line() for s in report.created][:7] == [
        "created org:org_demo",
        "created org_keys:org_demo/v1",
        "created user:u_demo_hradmin (HR_ADMIN)",
        "created user:u_demo_hr (HR)",
        "created user:u_demo_mgr_priya (MANAGER)",
        "created user:u_demo_mgr_arjun (MANAGER)",
        "created agent:approvals_agent/v1",
    ]
    assert report.steps[-1].line() == "created template:engineer-india/v1"
    assert report.unchanged == ()


def test_every_seeded_user_can_log_in_with_the_password_from_the_environment(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))

    async def check(user: SeedUser) -> None:
        entry = await world.users.find_login(user.email)
        assert entry is not None
        assert (entry.org_id, entry.user_id) == (ORG, user.id)
        with tenant_scope(ORG):
            record = await world.users.get_user(user.id)
        assert record is not None
        assert record.role is user.role
        assert verify_password(user.password, record.password_hash, rounds=ROUNDS)

    for user in plan.users:
        asyncio.run(check(user))


def test_no_password_is_stored_only_bcrypt_hashes(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))
    everything = repr(list(world.db.all_documents())) + repr(vars(world.template_store))
    for password in ENV.values():
        assert password not in everything
    assert all(u["password_hash"].startswith("$2") for u in world.db.raw("users"))


def test_the_registry_change_and_the_publish_are_audited_in_the_org_by_the_hr_admin(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))

    async def entries() -> list[Any]:
        with tenant_scope(ORG):
            return list((await world.audit.list(AuditQuery(), page=1, limit=200)).entries)

    audited = _run(entries)
    assert {e.actor for e in audited} == {Actor.user("u_demo_hradmin")}
    publish = [e for e in audited if e.action == "template.publish"]
    assert [(e.target, e.outcome) for e in publish] == [("template:engineer-india/v1", AuditOutcome.SUCCESS)]
    assert any(e.action == "registry.agent_register" for e in audited)


# --- box 2: a second run changes nothing ----------------------------------------------------------


def test_a_second_run_changes_nothing(plan: SeedPlan) -> None:
    world = _world()
    first = _run(lambda: world.seeder().run(plan))
    before = _snapshot(world)
    second = _run(lambda: world.seeder().run(plan))

    assert _snapshot(world) == before
    assert second.created == ()
    assert [s.target for s in second.steps] == [
        "org:org_demo",
        "org_keys:org_demo/v1",
        *(s.target for s in first.steps[2:6]),
        *(f"agent:{m.name}" for m in sorted(load_manifests(CONFIG / "agents"), key=lambda m: m.name)),
        "template:engineer-india/v1",
    ]
    assert second.render().endswith(f"0 created, {len(second.steps)} unchanged")


def test_a_run_that_failed_at_the_publish_is_finished_by_the_next_run_without_a_second_draft(plan: SeedPlan) -> None:
    class FailingPublishAudit(InMemoryAuditLog):
        fail = True

        async def audit(
            self, *, actor: Actor, action: str, target: str, outcome: AuditOutcome, reason: str | None = None
        ) -> str:
            if self.fail and action == "template.publish":
                raise RuntimeError("audit store unavailable")
            return await super().audit(actor=actor, action=action, target=target, outcome=outcome, reason=reason)

    audit = FailingPublishAudit()
    world = _world(audit)
    with pytest.raises(RuntimeError, match="audit store unavailable"):
        _run(lambda: world.seeder().run(plan))
    assert _in_org(world.templates.list_templates) == []

    audit.fail = False
    report = _run(lambda: world.seeder().run(plan))
    assert [s.line() for s in report.created] == ["created template:engineer-india/v1"]
    drafts = _in_org(world.templates.list_drafts)
    assert [(d.status, d.published_version) for d in drafts] == [("published", 1)]


def test_an_existing_template_is_left_as_it_is(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))
    changed = plan.templates[0].model_copy(update={"name": "Engineers, India (edited by HR)"})

    async def publish_v2() -> None:
        with tenant_scope(ORG):
            draft = await world.templates.create_draft(changed, actor_id="u_demo_hr")
            await world.templates.publish(draft.id, actor_id="u_demo_hradmin")

    _run(publish_v2)
    report = _run(lambda: world.seeder().run(plan))
    assert report.created == ()
    assert report.steps[-1].line() == "unchanged template:engineer-india/v2"
    summaries = _in_org(world.templates.list_templates)
    assert [(t.latest_version, t.name) for t in summaries] == [(2, "Engineers, India (edited by HR)")]


def test_an_existing_user_keeps_its_password(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))
    rotated = load_seed(CONFIG / "seed.yaml", {**ENV, "SEED_HR_PASSWORD": "a-new-password-987654"})
    report = _run(lambda: world.seeder().run(rotated))
    assert report.created == ()
    record = _in_org(lambda: world.users.get_user("u_demo_hr"))
    assert record is not None
    assert verify_password(ENV["SEED_HR_PASSWORD"], record.password_hash, rounds=ROUNDS)


# --- refusals ------------------------------------------------------------------------------------


def test_an_email_registered_to_another_user_stops_the_seed(plan: SeedPlan) -> None:
    world = _world()

    async def taken() -> None:
        with tenant_scope("org_other"):
            new = NewUser(email="HR@demo.example", name="Someone", role=Role.HR, password_hash=OTHER_HASH)
            await world.users.add_user(new, user_id="u_other")

    _run(taken)
    with pytest.raises(SeedConflictError, match="u_demo_hr is registered to another user"):
        _run(lambda: world.seeder().run(plan))
    assert world.db.raw("template_versions") == []
    assert _in_org(world.templates.list_templates) == []


def test_a_stored_publisher_who_is_not_hr_admin_publishes_nothing(plan: SeedPlan) -> None:
    world = _world()

    async def demoted_admin() -> None:
        with tenant_scope(ORG):
            new = NewUser(email="hr.admin@demo.example", name="Asha Menon", role=Role.HR, password_hash=OTHER_HASH)
            await world.users.add_user(new, user_id="u_demo_hradmin")

    _run(demoted_admin)
    with pytest.raises(SeedConflictError, match="u_demo_hradmin is not an HR_ADMIN"):
        _run(lambda: world.seeder().run(plan))
    assert _run(world.registry.list_agents) == []
    assert _in_org(world.templates.list_templates) == []


def test_a_login_entry_without_its_user_record_stops_the_seed(plan: SeedPlan) -> None:
    world = _world()
    _run(lambda: world.seeder().run(plan))
    world.db["users"].documents = [d for d in world.db["users"].documents if d["_id"] != "u_demo_mgr_arjun"]
    with pytest.raises(SeedConflictError, match="u_demo_mgr_arjun has a login entry but no user record"):
        _run(lambda: world.seeder().run(plan))


def test_org_keys_made_under_another_master_key_stop_the_seed(plan: SeedPlan) -> None:
    world = _world()
    _in_org(lambda: memory_keyring(world.store).create(ORG))
    with pytest.raises(KeyUnwrapError):
        _run(lambda: world.seeder().run(plan))
    assert world.db.raw("users") == []


def test_a_seed_racing_another_for_the_org_record_takes_the_existing_one(plan: SeedPlan) -> None:
    world = _world()
    orgs = world.store.shared("orgs")

    class RacingOrgs:
        """The first look sees nothing, as if the other seed inserted between the look and the insert."""

        raced = False

        async def find_one(self, query: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
            if not self.raced:
                self.raced = True
                await orgs.insert_one({"_id": ORG, "name": "Inserted by the other seed"})
                return None
            return await orgs.find_one(query)

        async def insert_one(self, document: Mapping[str, Any]) -> Any:
            return await orgs.insert_one(document)

    racing = Seeder(replace(world.services(), orgs=RacingOrgs()), bcrypt_rounds=ROUNDS)
    report = _run(lambda: racing.run(plan))
    assert report.steps[0].line() == "unchanged org:org_demo"
    assert world.db.raw("orgs") == [{"_id": ORG, "name": "Inserted by the other seed"}]
