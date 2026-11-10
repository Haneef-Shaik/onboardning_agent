"""Log-record scrubbing (PRD §7.10 "Logs & traces"): raw PII never reaches a log line; tokens do.

A record is scrubbed into a new record: the message is formatted with its arguments and redacted
with the PII-3 detectors, the exception and stack text are formatted and redacted (the exception
object, which may carry the value, is dropped), and every string `extra=` field is redacted.

`install_log_redaction()` (called by `init_telemetry`) covers a process in two places:
- `logging.Logger.makeRecord`, so every record any logger makes is scrubbed as soon as it exists,
  `extra=` fields included, whatever handler later receives it (non-propagating loggers such as
  uvicorn's, and handlers added after start-up, too);
- a `PiiScrubbingLogFilter` on each handler the root logger has at install time, a backstop for
  records made some other way (`logging.makeLogRecord`, a Logger subclass overriding makeRecord).

Fail closed, never break the caller: if scrubbing raises, the record is replaced by one that says
it was withheld (from `makeRecord`) or is dropped (by the filter). It never goes out unscrubbed.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from onb_guardrails.pii import redact

# Attributes every LogRecord has; anything else on a record came in through `extra=`.
_STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_formatter = logging.Formatter()

MakeRecord = Callable[..., logging.LogRecord]


def scrub_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """A scrubbed copy of `record`; the original is not changed. Raises if a detector does."""
    fields = dict(record.__dict__)
    fields["msg"] = _scrubbed_message(record)
    fields["args"] = ()
    fields["exc_text"] = _scrubbed_exception_text(record)
    fields["exc_info"] = None
    fields["stack_info"] = redact(record.stack_info) if record.stack_info else record.stack_info
    fields.pop("message", None)  # a cached formatted message would still hold the raw value
    for name, value in record.__dict__.items():
        if name not in _STANDARD_FIELDS and isinstance(value, str):
            fields[name] = redact(value)
    return _with_fields(record, fields)


def withheld_log_record(record: logging.LogRecord, error: Exception) -> logging.LogRecord:
    """A copy of `record` keeping only where it came from: no message, arguments, extras or traceback."""
    fields = {name: value for name, value in record.__dict__.items() if name in _STANDARD_FIELDS}
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
    Drops the record if scrubbing fails."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord | bool:
        try:
            return scrub_log_record(record)
        except Exception:
            return False


@dataclass(frozen=True)
class LogRedaction:
    """What `install_log_redaction` changed, so `uninstall()` can put it back."""

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


def _scrubbed_message(record: logging.LogRecord) -> str:
    try:
        message = record.getMessage()
    except Exception as exc:
        # The arguments do not fit the format; keep them, redacted, so the line is not lost.
        return redact(f"{record.msg} {record.args!r} [log arguments could not be formatted: {type(exc).__name__}]")
    return redact(message)


def _scrubbed_exception_text(record: logging.LogRecord) -> str | None:
    text = record.exc_text
    if not text and record.exc_info and record.exc_info[0] is not None:
        text = _formatter.formatException(record.exc_info)
    return redact(text) if text else text
