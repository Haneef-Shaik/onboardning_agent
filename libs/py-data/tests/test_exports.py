"""Box 4: `py-data` exports no Collection or Database object (PRD §7.9 Data access).

Callers reach MongoDB only through repositories. Nothing public in the package, in any of its
modules, or on a store or repository instance may be a PyMongo client, database or collection,
neither an instance nor the class itself.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from types import ModuleType
from typing import Any

import pymongo
import pytest
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.collection import Collection
from pymongo.database import Database

import onb_data
from onb_data import GlobalRepository, TenantRepository, connect

from .fakes import make_store

FORBIDDEN: tuple[type[Any], ...] = (
    AsyncCollection,
    AsyncDatabase,
    pymongo.AsyncMongoClient,
    Collection,
    Database,
    pymongo.MongoClient,
)


def _modules() -> list[ModuleType]:
    found: list[ModuleType] = [onb_data]
    for info in pkgutil.walk_packages(onb_data.__path__, prefix="onb_data."):
        found.append(importlib.import_module(info.name))
    return found


def _is_forbidden(value: object) -> bool:
    if isinstance(value, FORBIDDEN):
        return True
    return isinstance(value, type) and issubclass(value, FORBIDDEN)


def _public(obj: object) -> dict[str, object]:
    return {name: getattr(obj, name) for name in dir(obj) if not name.startswith("_")}


def test_the_forbidden_check_recognises_pymongo_objects() -> None:
    assert _is_forbidden(AsyncCollection)
    assert _is_forbidden(pymongo.MongoClient("mongodb://127.0.0.1:1", connect=False))
    assert not _is_forbidden(TenantRepository)


def test_the_package_has_submodules_to_scan() -> None:
    assert {m.__name__ for m in _modules()} >= {"onb_data", "onb_data.repository", "onb_data.store"}


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.__name__)
def test_no_module_exposes_a_collection_database_or_client(module: ModuleType) -> None:
    leaks = [name for name, value in _public(module).items() if _is_forbidden(value)]
    assert leaks == [], f"{module.__name__} exposes {leaks}"


def test_the_package_all_lists_no_pymongo_object() -> None:
    for name in onb_data.__all__:
        value = getattr(onb_data, name)
        assert not _is_forbidden(value), name
        assert not str(getattr(value, "__module__", "onb_data")).startswith("pymongo"), name


def test_a_connected_store_exposes_no_collection_database_or_client() -> None:
    async def main() -> list[str]:
        store = connect("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=1", "exports_check")
        try:
            return [name for name, value in _public(store).items() if _is_forbidden(value)]
        finally:
            await store.close()

    assert asyncio.run(main()) == []


def test_repositories_expose_no_collection_database_or_client() -> None:
    store = make_store()
    repos: list[TenantRepository | GlobalRepository] = [store.tenant("hires"), store.shared("orgs")]
    for repo in repos:
        leaks = [name for name, value in _public(repo).items() if _is_forbidden(value)]
        assert leaks == [], f"{type(repo).__name__} exposes {leaks}"
