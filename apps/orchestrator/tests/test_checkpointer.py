"""`TenantCheckpointSaver`: LangGraph checkpoints in MongoDB through tenant repositories (RUN-1;
PRD §7.1 Durability, §7.9 Orchestrator: `thread_id = org_id:run_id`, checkpoints carry `org_id`).

Over the real `DataStore` with an in-memory driver (`onb_api.testing_mongo`, test-only). The same
saver against the compose replica set is in `tests_int/`.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_data import NoTenantContextError, TenantViolationError, tenant_scope
from onb_orchestrator.checkpointer import CHECKPOINT_WRITES, CHECKPOINTS, TenantCheckpointSaver


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _config(thread: str, checkpoint_id: str | None = None, ns: str = "") -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": thread, "checkpoint_ns": ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id: str, value: int) -> Checkpoint:
    checkpoint = empty_checkpoint()
    return {**checkpoint, "id": checkpoint_id, "channel_values": {"count": value}, "channel_versions": {"count": 1}}


def _metadata(step: int) -> CheckpointMetadata:
    return {"source": "loop", "step": step, "parents": {}}


@pytest.fixture
def db() -> MemoryDatabase:
    return MemoryDatabase()


@pytest.fixture
def saver(db: MemoryDatabase) -> TenantCheckpointSaver:
    return TenantCheckpointSaver(memory_store(db))


async def _put_three(saver: TenantCheckpointSaver, thread: str) -> None:
    parent: str | None = None
    for n, cid in enumerate(("1ef0-a", "1ef0-b", "1ef0-c")):
        await saver.aput(_config(thread, parent), _checkpoint(cid, n), _metadata(n), {"count": 1})
        parent = cid


def test_a_checkpoint_round_trips_and_the_latest_is_returned(saver: TenantCheckpointSaver) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            latest = await saver.aget_tuple(_config("org_a:run_1"))
            assert latest is not None
            assert latest.checkpoint["id"] == "1ef0-c"
            assert latest.checkpoint["channel_values"] == {"count": 2}
            assert latest.metadata["step"] == 2
            assert latest.config["configurable"] == {
                "thread_id": "org_a:run_1",
                "checkpoint_ns": "",
                "checkpoint_id": "1ef0-c",
            }
            assert latest.parent_config is not None
            assert latest.parent_config["configurable"]["checkpoint_id"] == "1ef0-b"
            first = await saver.aget_tuple(_config("org_a:run_1", "1ef0-a"))
            assert first is not None
            assert first.checkpoint["channel_values"] == {"count": 0}
            assert first.parent_config is None
            assert await saver.aget_tuple(_config("org_a:run_1", "1ef0-zzz")) is None
            assert await saver.aget_tuple(_config("org_a:run_2")) is None

    run(go())


def test_list_is_newest_first_and_honours_before_limit_and_filter(saver: TenantCheckpointSaver) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            ids = [t.checkpoint["id"] async for t in saver.alist(_config("org_a:run_1"))]
            assert ids == ["1ef0-c", "1ef0-b", "1ef0-a"]
            before = [
                t.checkpoint["id"] async for t in saver.alist(_config("org_a:run_1"), before=_config("x", "1ef0-c"))
            ]
            assert before == ["1ef0-b", "1ef0-a"]
            limited = [t.checkpoint["id"] async for t in saver.alist(_config("org_a:run_1"), limit=1)]
            assert limited == ["1ef0-c"]
            step1 = [t.checkpoint["id"] async for t in saver.alist(_config("org_a:run_1"), filter={"step": 1})]
            assert step1 == ["1ef0-b"]
            one = [t.checkpoint["id"] async for t in saver.alist(_config("org_a:run_1", "1ef0-a"))]
            assert one == ["1ef0-a"]

    run(go())


def test_pending_writes_come_back_in_order_and_are_written_once(saver: TenantCheckpointSaver) -> None:
    async def go() -> list[tuple[str, str, Any]]:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            config = _config("org_a:run_1", "1ef0-c")
            await saver.aput_writes(config, [("finished", ["b"]), ("other", 1)], task_id="task-b", task_path="~b")
            await saver.aput_writes(config, [("finished", ["a"])], task_id="task-a", task_path="~a")
            # A repeat of a regular write (an at-least-once replay) keeps the first value.
            await saver.aput_writes(config, [("finished", ["changed"])], task_id="task-a", task_path="~a")
            latest = await saver.aget_tuple(_config("org_a:run_1"))
            assert latest is not None
            return list(latest.pending_writes or [])

    assert run(go()) == [("task-a", "finished", ["a"]), ("task-b", "finished", ["b"]), ("task-b", "other", 1)]


def test_a_task_whose_writes_were_cut_short_by_a_crash_is_not_half_applied(
    db: MemoryDatabase, saver: TenantCheckpointSaver
) -> None:
    """RUN-2 (found by the SIGKILL test): a task's writes are several documents, so a process killed between
    two of them leaves the task half recorded. LangGraph would take the first write as the task's whole
    output and never schedule what the missing one triggers (a run that ends with tasks never started).
    A task's writes come back only when all of them are stored; the replay then completes them."""

    async def pending() -> list[tuple[str, str, Any]]:
        latest = await saver.aget_tuple(_config("org_a:run_1"))
        assert latest is not None
        return list(latest.pending_writes or [])

    async def go() -> tuple[list[Any], list[Any], list[Any]]:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            config = _config("org_a:run_1", "1ef0-c")
            await saver.aput_writes(
                config, [("finished", ["c"]), ("branch:to:d", None)], task_id="task-c", task_path="~c"
            )
            await saver.aput_writes(config, [("finished", ["e"])], task_id="task-e", task_path="~e")
            complete = await pending()
            # The process died after task-c's first write: the second never reached the database.
            await store_delete(db, "branch:to:d")
            cut_short = await pending()
            # The task ran again (a takeover) and wrote its output once more: now it is whole.
            await saver.aput_writes(
                config, [("finished", ["c"]), ("branch:to:d", None)], task_id="task-c", task_path="~c"
            )
            return complete, cut_short, await pending()

    complete, cut_short, replayed = run(go())
    assert [w[:2] for w in complete] == [("task-c", "finished"), ("task-c", "branch:to:d"), ("task-e", "finished")]
    assert [w[:2] for w in cut_short] == [("task-e", "finished")], "task-c is not half applied"
    assert replayed == complete


async def store_delete(db: MemoryDatabase, channel: str) -> None:
    collection = db[CHECKPOINT_WRITES]
    collection.documents = [d for d in collection.documents if d["channel"] != channel]


def test_a_special_write_replaces_the_earlier_one(saver: TenantCheckpointSaver) -> None:
    async def go() -> list[tuple[str, str, Any]]:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            config = _config("org_a:run_1", "1ef0-c")
            await saver.aput_writes(config, [("__error__", "first")], task_id="t", task_path="~t")
            await saver.aput_writes(config, [("__error__", "second")], task_id="t", task_path="~t")
            latest = await saver.aget_tuple(config)
            assert latest is not None
            return list(latest.pending_writes or [])

    assert run(go()) == [("t", "__error__", "second")]


def test_every_checkpoint_document_carries_the_org_and_its_thread(
    db: MemoryDatabase, saver: TenantCheckpointSaver
) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            await saver.aput_writes(_config("org_a:run_1", "1ef0-a"), [("x", 1)], task_id="t")

    run(go())
    documents = db.raw(CHECKPOINTS) + db.raw(CHECKPOINT_WRITES)
    assert len(documents) == 4
    assert {(d["org_id"], d["thread_id"]) for d in documents} == {("org_a", "org_a:run_1")}


@pytest.mark.parametrize("thread", ["org_b:run_1", "run_1", "org_a", "org_a:", ":run_1", "org_a:run:1", 7])
def test_a_thread_that_is_not_org_id_colon_run_id_of_the_tenant_is_refused(
    saver: TenantCheckpointSaver, thread: Any
) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            with pytest.raises(TenantViolationError):
                await saver.aput(_config(thread), _checkpoint("1", 0), _metadata(0), {})
            with pytest.raises(TenantViolationError):
                await saver.aget_tuple(_config(thread))
            with pytest.raises(TenantViolationError):
                await saver.aput_writes(_config(thread, "1"), [("x", 1)], task_id="t")
            with pytest.raises(TenantViolationError):
                _ = [t async for t in saver.alist(_config(thread))]

    run(go())


def test_listing_without_a_thread_is_refused(saver: TenantCheckpointSaver) -> None:
    async def go() -> None:
        with tenant_scope("org_a"), pytest.raises(TenantViolationError):
            _ = [t async for t in saver.alist(None)]

    run(go())


def test_the_saver_needs_a_tenant_context(saver: TenantCheckpointSaver) -> None:
    with pytest.raises(NoTenantContextError):
        run(saver.aget_tuple(_config("org_a:run_1")))


def test_another_org_never_sees_a_checkpoint_even_under_the_same_run_id(saver: TenantCheckpointSaver) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
        with tenant_scope("org_b"):
            assert await saver.aget_tuple(_config("org_b:run_1")) is None
            assert [t async for t in saver.alist(_config("org_b:run_1"))] == []

    run(go())


def test_indexes_start_with_org_id_and_are_unique_per_checkpoint_and_write(
    db: MemoryDatabase, saver: TenantCheckpointSaver
) -> None:
    run(saver.ensure_indexes())
    found = {(name, index.keys, index.unique) for name, coll in db.collections.items() for index in coll.indexes}
    assert found == {
        (CHECKPOINTS, ("org_id", "thread_id", "checkpoint_ns", "checkpoint_id"), True),
        (CHECKPOINT_WRITES, ("org_id", "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"), True),
    }


def test_the_sync_interface_is_not_offered(saver: TenantCheckpointSaver) -> None:
    with pytest.raises(NotImplementedError):
        saver.get_tuple(_config("org_a:run_1"))
    with pytest.raises(NotImplementedError):
        list(saver.list(_config("org_a:run_1")))
    with pytest.raises(NotImplementedError):
        saver.put(_config("org_a:run_1"), _checkpoint("1", 0), _metadata(0), {})
    with pytest.raises(NotImplementedError):
        saver.put_writes(_config("org_a:run_1", "1"), [], task_id="t")


class Counter(TypedDict):
    steps: Annotated[list[str], operator.add]


def test_a_langgraph_graph_checkpoints_through_the_saver_and_resumes_its_state(
    db: MemoryDatabase, saver: TenantCheckpointSaver
) -> None:
    builder = StateGraph(Counter)
    builder.add_node("one", lambda _: {"steps": ["one"]})
    builder.add_node("two", lambda _: {"steps": ["two"]})
    builder.add_edge(START, "one")
    builder.add_edge("one", "two")
    builder.add_edge("two", END)
    graph = builder.compile(checkpointer=saver)
    config = _config("org_a:run_g")

    async def go() -> tuple[list[str], tuple[str, ...]]:
        with tenant_scope("org_a"):
            result = await graph.ainvoke({"steps": []}, config)
            state = await graph.aget_state(config)
            return result["steps"], state.next

    steps, pending = run(go())
    assert steps == ["one", "two"]
    assert pending == ()
    assert {d["thread_id"] for d in db.raw(CHECKPOINTS)} == {"org_a:run_g"}
    assert {d["org_id"] for d in db.raw(CHECKPOINTS)} == {"org_a"}


@dataclass
class Planted:
    """A type someone with write access to the checkpoint collections might plant in a blob."""

    command: str

    def __post_init__(self) -> None:
        PLANTED_BUILT.append(self.command)


PLANTED_BUILT: list[str] = []


def test_a_planted_type_in_a_checkpoint_blob_is_never_built(saver: TenantCheckpointSaver) -> None:
    """The saver loads checkpoints with LangGraph's strict msgpack allowlist: a stored blob naming an
    arbitrary module's type comes back as plain data, and nothing of that module is built or called."""
    blob = JsonPlusSerializer(allowed_msgpack_modules=True).dumps_typed({"x": Planted("echo")})
    PLANTED_BUILT.clear()
    loaded = saver.serde.loads_typed(blob)
    assert PLANTED_BUILT == []
    assert loaded == {"x": {"command": "echo"}}


def test_the_saver_still_round_trips_its_own_state(saver: TenantCheckpointSaver) -> None:
    state = {"finished": ["a", "b"], "n": 3}
    assert saver.serde.loads_typed(saver.serde.dumps_typed(state)) == state


# --- RUN-2: deleting a finished run's thread -------------------------------------------------------


def test_deleting_a_thread_removes_its_checkpoints_and_writes_and_no_other_threads(
    db: MemoryDatabase, saver: TenantCheckpointSaver
) -> None:
    async def go() -> None:
        with tenant_scope("org_a"):
            await _put_three(saver, "org_a:run_1")
            await _put_three(saver, "org_a:run_2")
            await saver.aput_writes(_config("org_a:run_1", "1ef0-c"), [("count", 9)], "task_1")
            await saver.aput_writes(_config("org_a:run_2", "1ef0-c"), [("count", 9)], "task_1")
            await saver.adelete_thread("org_a:run_1")
            assert await saver.aget_tuple(_config("org_a:run_1")) is None
            assert await saver.aget_tuple(_config("org_a:run_2")) is not None
            assert {d["thread_id"] for d in db.raw(CHECKPOINT_WRITES)} == {"org_a:run_2"}
            await saver.adelete_thread("org_a:run_1")  # deleting twice is not an error

    run(go())


def test_another_orgs_thread_is_refused_and_nothing_is_deleted(saver: TenantCheckpointSaver) -> None:
    async def go() -> None:
        with tenant_scope("org_b"):
            await _put_three(saver, "org_b:run_1")
        with tenant_scope("org_a"):
            with pytest.raises(TenantViolationError):
                await saver.adelete_thread("org_b:run_1")
            with pytest.raises(TenantViolationError):
                await saver.adelete_thread("run_1")
        with tenant_scope("org_b"):
            assert await saver.aget_tuple(_config("org_b:run_1")) is not None

    run(go())
