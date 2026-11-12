"""What `make test-pii` scans (SEC-1 card scope): every MongoDB collection except the vault, the application
log, every compose container's log, the session's traces exported from Jaeger, `mock_outbox`, the events,
and the captured LLM inputs. Each collector yields `scan.Item`s labelled by source."""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from pymongo.database import Database

from .scan import Item, flatten, flatten_json_lines

VAULT_COLLECTIONS: Final = frozenset({"pii_vault", "pii_vault_aadhaar"})
EVENT_COLLECTIONS: Final = frozenset({"events", "outbox", "jobs", "dead_letters"})
OUTBOX_COLLECTION: Final = "mock_outbox"
JAEGER_PAGE: Final = 1000
_RECORD_FIELDS: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def source_of(collection: str) -> str:
    if collection == OUTBOX_COLLECTION:
        return "mock_outbox"
    if collection in EVENT_COLLECTIONS or collection.startswith("events"):
        return "events"
    return "mongo"


def mongo_items(db: Database[dict[str, Any]]) -> Iterator[Item]:
    """Every document of every collection but the vault's, GridFS chunks included."""
    for name in sorted(db.list_collection_names()):
        if name in VAULT_COLLECTIONS:
            continue
        source = source_of(name)
        for index, document in enumerate(db[name].find()):
            yield from flatten(source, f"{name}[{index}]", document)


class JsonLogFormatter(logging.Formatter):
    """One JSON object per record: the formatted message, the exception text and every extra field, so
    the scan sees all a record carries (after the OBS-1 scrubber, as any handler would)."""

    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            body["exc_text"] = record.exc_text
        if record.stack_info:
            body["stack_info"] = record.stack_info
        body["extra"] = {k: v for k, v in record.__dict__.items() if k not in _RECORD_FIELDS}
        return json.dumps(body, default=str)


def log_file_items(path: Path) -> Iterator[Item]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    yield from flatten_json_lines("app_logs", path.name, lines)


def container_log_items(root: Path, *, since_us: int) -> Iterator[Item]:
    """`docker compose logs` of every service of this worktree's stack (the `.env` names the project), from
    the session's start: an earlier session's lines are not this run's."""
    services = _run(root, "docker", "compose", "ps", "--services").split()
    if not services:
        raise RuntimeError("no compose service is running: run `make up`")
    since = _rfc3339(since_us - 1_000_000)
    for service in services:
        argv = ("docker", "compose", "logs", "--no-color", "--no-log-prefix", "--since", since, service)
        yield from flatten_json_lines("container_logs", service, _run(root, *argv).splitlines())


def _run(root: Path, *argv: str) -> str:
    return subprocess.run(list(argv), cwd=root, capture_output=True, text=True, check=True).stdout


def _rfc3339(micros: int) -> str:
    return datetime.fromtimestamp(micros / 1_000_000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def jaeger_traces(jaeger: str, service: str, start_us: int, end_us: int) -> list[dict[str, Any]]:
    """Every span of `service` Jaeger holds between the two times, as OTLP-JSON `resourceSpans` (the
    query API v3, which is what Jaeger 2 serves)."""
    query = urllib.parse.urlencode(
        {
            "query.service_name": service,
            "query.start_time_min": _rfc3339(start_us),
            "query.start_time_max": _rfc3339(end_us),
            "query.search_depth": JAEGER_PAGE,
        }
    )
    try:
        with urllib.request.urlopen(f"http://{jaeger}/api/v3/traces?{query}", timeout=30) as response:
            body: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == HTTPStatus.NOT_FOUND:  # no trace yet
            return []
        raise
    spans: list[dict[str, Any]] = body.get("result", {}).get("resourceSpans") or []
    return spans


def wait_for_jaeger(jaeger: str, service: str, start_us: int, minimum_spans: int, timeout_s: float = 60) -> list[Any]:
    """Poll until Jaeger holds at least `minimum_spans` spans of the session (the collector batches), then
    once more until the count stops growing."""
    deadline = time.monotonic() + timeout_s
    traces: list[dict[str, Any]] = []
    seen = -1
    while time.monotonic() < deadline:
        traces = jaeger_traces(jaeger, service, start_us, int(time.time() * 1_000_000))
        count = span_count(traces)
        if count >= minimum_spans and count == seen:
            return traces
        seen = count
        time.sleep(3)
    return traces


def span_count(resource_spans: Sequence[dict[str, Any]]) -> int:
    return sum(len(scope.get("spans") or []) for rs in resource_spans for scope in rs.get("scopeSpans") or [])


def jaeger_items(resource_spans: Sequence[dict[str, Any]]) -> Iterator[Item]:
    for index, rs in enumerate(resource_spans):
        yield from flatten("jaeger", f"resourceSpans[{index}]", rs)


def trace_ids(resource_spans: Sequence[dict[str, Any]]) -> Iterator[str]:
    for rs in resource_spans:
        for scope in rs.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                yield str(span.get("traceId"))


def response_items(responses: Sequence[tuple[str, Any]]) -> Iterator[Item]:
    """Every API answer the journey got (reveals excluded: they answer with the value by design)."""
    for index, (route, body) in enumerate(responses):
        yield from flatten("api_responses", f"[{index}] {route}", body)


def llm_items(calls: Sequence[tuple[str, list[dict[str, Any]]]]) -> Iterator[Item]:
    for index, (model, messages) in enumerate(calls):
        yield from flatten("llm_inputs", f"call[{index}]:{model}", messages)
