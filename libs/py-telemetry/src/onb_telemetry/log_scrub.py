"""Log-record scrubbing (PRD §7.10 "Logs & traces"): raw PII never reaches a log line; tokens do.

A record is scrubbed into a new record (the original is untouched):
- the message: the template and each argument are scrubbed and kept apart, so formatters that read
  `record.args` (uvicorn's access log) still work; if the scrubbed pieces would still format into a
  value (one split between template and argument), the redacted formatted message is used instead,
  with no arguments;
- the exception and stack text are formatted and redacted. The exception object is dropped, since
  it carries the raw message, so a handler reading `exc_info` (OTel log export, Sentry) gets none;
- every `extra=` field: its name, and its value whatever the type (`scrub_values.scrub_object`).

`install_log_redaction()` (called once per process by `init_telemetry`) covers a process in two places:
- `logging.Logger.makeRecord`, so every record any logger makes is scrubbed as soon as it exists,
  `extra=` fields included, whatever handler later receives it (non-propagating loggers such as
  uvicorn's, and handlers added after start-up, too);
- a `PiiScrubbingLogFilter` on each handler the root logger has at install time, a backstop for
  records made some other way (`logging.makeLogRecord`, a Logger subclass overriding makeRecord).
  A handler added later only has the first; add the filter to it if it may see such records.
It stays installed for the life of the process (logging goes on after telemetry shuts down).

Fail closed, never break the caller: if scrubbing raises, the record is replaced by one that says
it was withheld (from `makeRecord`) or is dropped (by the filter). It never goes out unscrubbed.
"""

from __future__ import annotations

import copy
import logging
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from onb_guardrails.pii import redact

from onb_telemetry.scrub_values import scrub_key, scrub_object

# Attributes every LogRecord has; anything else on a record came in through `extra=`.
_STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_WITHHELD = "[withheld]"
_formatter = logging.Formatter()
_scrubbed: weakref.WeakSet[logging.LogRecord] = weakref.WeakSet()  # made here, so the filter skips them

MakeRecord = Callable[..., logging.LogRecord]


def scrub_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """A scrubbed copy of `record`; the original is not changed. Raises if a detector does."""
    fields = {name: value for name, value in record.__dict__.items() if name in _STANDARD_FIELDS}
    fields["msg"], fields["args"] = _scrubbed_message(record)
    fields["exc_text"] = _scrubbed_exception_text(record)
    fields["exc_info"] = None
    fields["stack_info"] = redact(record.stack_info) if record.stack_info else record.stack_info
    fields.pop("message", None)  # a cached formatted message would still hold the raw value
    for name, value in record.__dict__.items():
        if name not in _STANDARD_FIELDS:
            fields[scrub_key(name)] = scrub_object(name, value)
    scrubbed = _with_fields(record, fields)
    _scrubbed.add(scrubbed)
    return scrubbed


def withheld_log_record(record: logging.LogRecord, error: Exception) -> logging.LogRecord:
    """A copy of `record` keeping only where it came from. The message, arguments and traceback are
    gone; each `extra=` field is kept by a fixed placeholder (its name too, if that held PII), so a
    formatter expecting it (`%(otelTraceID)s`) still formats."""
    fields: dict[str, Any] = {}
    for name, value in record.__dict__.items():
        if name in _STANDARD_FIELDS:
            fields[name] = value
        else:
            fields[_withheld_name(name)] = _WITHHELD
    fields.pop("message", None)
    fields.update(
        msg=f"[log record withheld: PII scrubbing failed with {type(error).__name__}]",
        args=(),
        exc_info=None,
        exc_text=None,
        stack_info=None,
    )
    return _with_fields(record, fields)


class PiiScrubbingLogFilter(logging.Filter):
    """Hands the handler a scrubbed copy of each record (Python 3.12 filters may return a record).
    Records `scrub_log_record` made pass as they are. Drops the record if scrubbing fails."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord | bool:
        if record in _scrubbed:
            return record
        try:
            return scrub_log_record(record)
        except Exception:
            return False


@dataclass(frozen=True)
class LogRedaction:
    """What `install_log_redaction` changed, so `uninstall()` (tests only) can put it back."""

    previous_make_record: MakeRecord
    scrubbing_make_record: MakeRecord
    log_filter: PiiScrubbingLogFilter
    handlers: tuple[logging.Handler, ...]

    def uninstall(self) -> None:
        for handler in self.handlers:
            handler.removeFilter(self.log_filter)
        if logging.Logger.makeRecord is self.scrubbing_make_record:
            logging.Logger.makeRecord = self.previous_make_record  # type: ignore[method-assign]  # restoring the original


def install_log_redaction() -> LogRedaction:
    """Scrub every log record made from now on, and every record reaching the root logger's handlers."""
    previous: MakeRecord = logging.Logger.makeRecord

    def scrubbing_make_record(self: logging.Logger, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(self, *args, **kwargs)
        try:
            return scrub_log_record(record)
        except Exception as error:
            return withheld_log_record(record, error)

    log_filter = PiiScrubbingLogFilter()
    handlers = tuple(logging.getLogger().handlers)
    for handler in handlers:
        handler.addFilter(log_filter)
    # Patching the class method is the one place that sees `extra=` fields for every logger.
    logging.Logger.makeRecord = scrubbing_make_record  # type: ignore[method-assign]  # see above
    return LogRedaction(previous, scrubbing_make_record, log_filter, handlers)


def _with_fields(record: logging.LogRecord, fields: dict[str, Any]) -> logging.LogRecord:
    new = copy.copy(record)  # not makeLogRecord: keep the record's class
    new.__dict__ = fields
    return new


def _scrubbed_message(record: logging.LogRecord) -> tuple[object, object]:
    """(msg, args) for the scrubbed record: kept apart when that is safe, else formatted and redacted."""
    try:
        full = redact(record.getMessage())
    except Exception as exc:
        # The arguments do not fit the format; keep them, redacted, so the line is not lost.
        return redact(f"{record.msg} {record.args!r} [log arguments could not be formatted: {type(exc).__name__}]"), ()
    if not record.args:
        return full, ()
    template = redact(str(record.msg))
    args = scrub_object(None, record.args)
    try:
        rejoined = template % (args if isinstance(args, Mapping) else tuple(args))  # type: ignore[arg-type]  # tuple or mapping, as LogRecord.args
    except Exception:
        return full, ()
    if redact(rejoined) != rejoined:
        return full, ()  # a value split across template and argument only shows once they are joined
    return template, args


def _scrubbed_exception_text(record: logging.LogRecord) -> str | None:
    text = record.exc_text
    if not text and record.exc_info and record.exc_info[0] is not None:
        text = _formatter.formatException(record.exc_info)
    return redact(text) if text else text


def _withheld_name(name: str) -> str:
    try:
        return scrub_key(name)
    except Exception:
        return name if name.isidentifier() and name.isascii() and not any(c.isdigit() for c in name) else _WITHHELD
