"""Document scans against a real MongoDB (HIRE-2 boxes 1-3; PRD FR-P3, §7.10 Documents).

Each test uses a scratch database on the compose Mongo, dropped afterwards. Read-backs and tampering use
the root client inside `unscoped(...)`, so nothing trusts the API to report on itself. The scans are
synthetic bytes behind a real PNG signature.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient
from onb_api import create_app
from onb_api.documents.bucket import CHUNKS_COLLECTION, FILES_COLLECTION
from onb_api.documents.meta import META_COLLECTION
from onb_api.documents.service import DocumentService
from onb_api.hires.service import HireService
from onb_api.testing import TEST_SETTINGS, bearer, seeded_store, token_for
from onb_contracts.auth import Role
from onb_contracts.documents import MAX_DOCUMENT_BYTES
from onb_crypto import ORG_KEYS_COLLECTION, DecryptionError, Keyring, MasterKey, RepositoryOrgKeyStore
from onb_data import AuditLog, connect, tenant_scope
from pymongo import MongoClient

Client = MongoClient[dict[str, Any]]
Unscoped = Callable[[str], AbstractContextManager[None]]
SEED = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "seed_v1.json"

HR = ("u_acme_hr1", "org_acme", Role.HR)
HIRE = "h_acme_001"
MARKER = b"SYNTHETIC-PAN-SCAN-BQVPS4821K"
SCAN = b"\x89PNG\r\n\x1a\n" + MARKER + secrets.token_bytes(600 * 1024)  # three GridFS chunks


@dataclass(frozen=True)
class Stack:
    client: TestClient
    scratch: tuple[str, str]
    master: MasterKey


def _uri(root_client: Client) -> str:
    host, port = root_client.address or ("127.0.0.1", 27017)
    user = quote_plus(os.environ["MONGO_ROOT_USERNAME"])
    password = quote_plus(os.environ["MONGO_ROOT_PASSWORD"])
    return f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin&directConnection=true"


@pytest.fixture
def scratch(root_client: Client, unscoped: Unscoped) -> Iterator[tuple[str, str]]:
    name = f"it_hire2_{uuid.uuid4().hex[:12]}"
    seeded = json.loads(SEED.read_text())["new_hires"]
    with unscoped("seeding seed_v1's hires of both orgs as stored documents, before the API starts"):
        root_client[name]["new_hires"].insert_many([dict(doc) for doc in seeded])
    yield _uri(root_client), name
    root_client.drop_database(name)


@pytest.fixture
def stack(scratch: tuple[str, str]) -> Iterator[Stack]:
    store = connect(*scratch)
    master = MasterKey.from_encoded(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    keyring = Keyring(master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
    users = asyncio.run(seeded_store())
    audit = AuditLog(store)
    hires = HireService.build(store, keyring, auditor=audit, users=users)
    documents = DocumentService.build(store, keyring, auditor=audit)
    app = create_app(auth=TEST_SETTINGS, users=users, audit=audit, hires=hires, documents=documents)
    with TestClient(app) as client:
        yield Stack(client, scratch, master)


def _upload(client: TestClient, data: bytes = SCAN) -> Any:
    return client.post(
        f"/hires/{HIRE}/documents",
        params={"type": "pan"},
        content=data,
        headers={**bearer(token_for(*HR)), "Content-Type": "image/png"},
    )


def _read(stack: Stack, hire_id: str, document_id: str) -> bytes:
    """Through a document service of its own (the app's client is bound to the app's event loop), under
    the same master key."""

    async def scoped() -> bytes:
        store = connect(*stack.scratch)
        try:
            keyring = Keyring(stack.master, RepositoryOrgKeyStore(store.tenant(ORG_KEYS_COLLECTION)))
            documents = DocumentService.build(store, keyring, auditor=AuditLog(store))
            with tenant_scope("org_acme"):
                return await documents.read("org_acme", hire_id=hire_id, document_id=document_id)
        finally:
            await store.close()

    return asyncio.run(scoped())


def test_gridfs_holds_ciphertext_that_opens_only_for_the_hire(
    stack: Stack, root_client: Client, scratch: tuple[str, str], unscoped: Unscoped
) -> None:
    """Box 1 on Mongo: the stored chunk bytes are not the scan; the scan opens for its hire only."""
    response = _upload(stack.client)
    assert response.status_code == 201, response.text
    document_id = response.json()["data"]["id"]
    db = root_client[scratch[1]]
    with unscoped("root read-back of the stored chunks and every collection, to scan for the plaintext"):
        chunks = list(db[CHUNKS_COLLECTION].find({"files_id": document_id}).sort("n", 1))
        files = db[FILES_COLLECTION].find_one({"_id": document_id})
        everything = [doc for name in db.list_collection_names() for doc in db[name].find({})]
        indexes = db[CHUNKS_COLLECTION].index_information()
    stored = b"".join(bytes(c["data"]) for c in chunks)
    assert files is not None
    assert len(chunks) == 3
    assert files["length"] == len(stored) == len(SCAN) + 16
    assert stored != SCAN
    assert MARKER not in stored
    assert all(MARKER not in repr(doc).encode() for doc in everything)
    assert indexes["org_files_id_n_unique"]["unique"] is True
    assert _read(stack, HIRE, document_id) == SCAN
    with pytest.raises(LookupError):
        _read(stack, "h_acme_002", document_id)
    with unscoped("tampering: re-pointing the stored scan at another hire, as a direct DB edit would"):
        db[FILES_COLLECTION].update_one({"_id": document_id}, {"$set": {"metadata.hire_id": "h_acme_002"}})
    with pytest.raises(DecryptionError):
        _read(stack, "h_acme_002", document_id)


def test_documents_meta_on_mongo_holds_only_ids_type_uploaded_at_and_verified(
    stack: Stack, root_client: Client, scratch: tuple[str, str], unscoped: Unscoped
) -> None:
    """Box 3 on Mongo."""
    document_id = _upload(stack.client).json()["data"]["id"]
    with unscoped("root read-back of documents_meta"):
        (meta,) = list(root_client[scratch[1]][META_COLLECTION].find({}))
    assert set(meta) == {"_id", "org_id", "hire_id", "type", "uploaded_at", "verified"}
    assert (meta["_id"], meta["org_id"], meta["hire_id"], meta["type"], meta["verified"]) == (
        document_id,
        "org_acme",
        HIRE,
        "pan",
        False,
    )


def test_an_oversized_scan_stores_nothing_on_mongo(
    stack: Stack, root_client: Client, scratch: tuple[str, str], unscoped: Unscoped
) -> None:
    """Box 2 on Mongo."""
    response = _upload(stack.client, b"\x89PNG\r\n\x1a\n" + bytes(MAX_DOCUMENT_BYTES))
    assert response.status_code == 413, response.text
    db = root_client[scratch[1]]
    with unscoped("root read-back: nothing stored for a refused upload"):
        counts = [db[name].count_documents({}) for name in (CHUNKS_COLLECTION, FILES_COLLECTION, META_COLLECTION)]
    assert counts == [0, 0, 0]
