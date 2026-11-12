"""LangGraph checkpoints in MongoDB, through tenant repositories only (PRD §7.1 Durability, §7.9).

The PRD names `langgraph-checkpoint-mongodb` for durability. That saver writes its own collections
through a raw client, with filters on `thread_id` alone; §7.9 requires every database access to go
through `TenantRepository` and every checkpoint to carry `org_id`, and the T-2 safety net fails any
command on a tenant collection without `org_id`. So this saver keeps the same storage model (one
document per checkpoint, one per pending write, LangGraph's own serializer) on `onb_data` tenant
repositories (ADR 0015):

- Every document carries `org_id` (set by the repository) and every read is filtered by it.
- The thread must be `"{org_id}:{run_id}"` for the org in the tenant context; any other thread id
  (another org's, a bare run id, a malformed one) raises `TenantViolationError` before any I/O.
- Indexes start with `org_id`: one checkpoint per (thread, namespace, id), one write per
  (thread, namespace, checkpoint, task, index).

- Checkpoints load with LangGraph's strict msgpack allowlist (`allowed_msgpack_modules=None`): a
  blob naming any other type loads as plain data, so a planted document cannot run code.
- Listing needs a thread: `alist(None)` (every thread) raises `TenantViolationError`, so LangGraph
  tooling that lists across threads is refused rather than reading past one run.

Only the async interface is offered (the orchestrator is async end to end); the sync methods raise
`NotImplementedError`, as LangGraph's base class does. Deleting threads is not offered yet (retention
belongs with RUN-2 and the ops goals).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from onb_data import DataStore, TenantViolationError, current_tenant

__all__ = ["CHECKPOINTS", "CHECKPOINT_WRITES", "TenantCheckpointSaver"]

CHECKPOINTS: Final = "run_checkpoints"
CHECKPOINT_WRITES: Final = "run_checkpoint_writes"
_SEPARATOR: Final = ":"


def _thread(config: RunnableConfig | None) -> str:
    """The config's thread id, checked against the tenant: `"{org_id}:{run_id}"`, nothing else."""
    org_id = current_tenant().org_id
    configurable = (config or {}).get("configurable") or {}
    thread = configurable.get("thread_id")
    if not isinstance(thread, str):
        raise TenantViolationError("a checkpoint thread id is required")
    org, separator, run_id = thread.partition(_SEPARATOR)
    if separator != _SEPARATOR or org != org_id or not run_id or _SEPARATOR in run_id:
        raise TenantViolationError("a checkpoint thread must be '<org_id>:<run_id>' for the tenant's org")
    return thread


def _namespace(config: RunnableConfig | None) -> str:
    namespace = ((config or {}).get("configurable") or {}).get("checkpoint_ns", "")
    return namespace if isinstance(namespace, str) else ""


def _config(thread: str, namespace: str, checkpoint_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}}


class TenantCheckpointSaver(BaseCheckpointSaver[int]):
    def __init__(self, store: DataStore) -> None:
        # Strict msgpack: a stored blob naming a type outside LangGraph's safe built-ins loads as plain
        # data; nothing it names is imported or called (a planted checkpoint cannot run code).
        super().__init__(serde=JsonPlusSerializer(allowed_msgpack_modules=None))
        self._store = store
        self._checkpoints = store.tenant(CHECKPOINTS)
        self._writes = store.tenant(CHECKPOINT_WRITES)

    async def ensure_indexes(self) -> None:
        await self._store.ensure_index(
            CHECKPOINTS, [("org_id", 1), ("thread_id", 1), ("checkpoint_ns", 1), ("checkpoint_id", 1)], unique=True
        )
        await self._store.ensure_index(
            CHECKPOINT_WRITES,
            [("org_id", 1), ("thread_id", 1), ("checkpoint_ns", 1), ("checkpoint_id", 1), ("task_id", 1), ("idx", 1)],
            unique=True,
        )

    # --- reads -----------------------------------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread, namespace = _thread(config), _namespace(config)
        query: dict[str, Any] = {"thread_id": thread, "checkpoint_ns": namespace}
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            query["checkpoint_id"] = checkpoint_id
        found = await self._checkpoints.find(query, sort=[("checkpoint_id", -1)], limit=1)
        return await self._tuple(found[0]) if found else None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query: dict[str, Any] = {"thread_id": _thread(config)}
        configurable = (config or {}).get("configurable") or {}
        if "checkpoint_ns" in configurable:
            query["checkpoint_ns"] = _namespace(config)
        checkpoint_id = get_checkpoint_id(config) if config else None
        if checkpoint_id:
            query["checkpoint_id"] = checkpoint_id
        before_id = get_checkpoint_id(before) if before else None
        if before_id:
            query["checkpoint_id"] = {"$lt": before_id}
        remaining = limit
        for document in await self._checkpoints.find(query, sort=[("checkpoint_id", -1)]):
            if remaining is not None and remaining <= 0:
                return
            found = await self._tuple(document)
            if filter and not all(found.metadata.get(key) == value for key, value in filter.items()):
                continue
            if remaining is not None:
                remaining -= 1
            yield found

    async def _tuple(self, document: Mapping[str, Any]) -> CheckpointTuple:
        thread, namespace, checkpoint_id = document["thread_id"], document["checkpoint_ns"], document["checkpoint_id"]
        writes = await self._writes.find(
            {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id},
            sort=[("task_path", 1), ("task_id", 1), ("idx", 1)],
        )
        parent = document.get("parent_checkpoint_id")
        return CheckpointTuple(
            config=_config(thread, namespace, checkpoint_id),
            checkpoint=self.serde.loads_typed((document["type"], document["checkpoint"])),
            metadata=self.serde.loads_typed((document["metadata_type"], document["metadata"])),
            parent_config=_config(thread, namespace, parent) if parent else None,
            pending_writes=[
                (w["task_id"], w["channel"], self.serde.loads_typed((w["type"], w["value"]))) for w in writes
            ],
        )

    # --- writes ----------------------------------------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread, namespace = _thread(config), _namespace(config)
        checkpoint_type, serialized = self.serde.dumps_typed(checkpoint)
        metadata_type, serialized_metadata = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        key = {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint["id"]}
        await self._checkpoints.update_one(
            key,
            {
                "$set": {
                    "parent_checkpoint_id": get_checkpoint_id(config),
                    "type": checkpoint_type,
                    "checkpoint": serialized,
                    "metadata_type": metadata_type,
                    "metadata": serialized_metadata,
                }
            },
            upsert=True,
        )
        return _config(thread, namespace, checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread, namespace = _thread(config), _namespace(config)
        checkpoint_id = get_checkpoint_id(config)
        # A special channel (error, interrupt, ...) replaces its earlier write; a regular write is
        # kept as first written, so a replayed task cannot change what was recorded.
        operator = "$set" if all(channel in WRITES_IDX_MAP for channel, _ in writes) else "$setOnInsert"
        for idx, (channel, value) in enumerate(writes):
            value_type, serialized = self.serde.dumps_typed(value)
            key = {
                "thread_id": thread,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "idx": WRITES_IDX_MAP.get(channel, idx),
            }
            fields = {"task_path": task_path, "channel": channel, "type": value_type, "value": serialized}
            await self._writes.update_one(key, {operator: fields}, upsert=True)
