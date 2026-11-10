"""OBS-1 mutation checks: break each guard, run the telemetry unit tests, record what failed, restore.

Run from the repo root: `uv run --frozen python docs/evidence/OBS-1/mutations.py`.
Each mutation replaces one exact snippet (which must occur once) and must make at least one named
test fail. The source tree is restored after each mutation and checked clean at the end.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SRC = Path("libs/py-telemetry/src/onb_telemetry")
SPAN, LOG, SETUP, PIPE = SRC / "span_scrub.py", SRC / "log_scrub.py", SRC / "setup.py", SRC / "pipeline.py"
VALUES = SRC / "scrub_values.py"

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    # --- spans
    ("M1", "init installs no scrubber", SETUP,
     "HookedSpanProcessor(export, final_hook=PiiScrubbingSpanProcessor())", "HookedSpanProcessor(export)"),
    ("M2", "scrubber runs before the hooks, not after", PIPE,
     "return self._hooks if final is None else (*self._hooks, final)",
     "return self._hooks if final is None else (final, *self._hooks)"),
    ("M3", "attribute values not scrubbed", VALUES,
     "return {scrub_key(key): scrub_attribute(key, value) for", "return {scrub_key(key): value for"),
    ("M4", "attribute keys not scrubbed", VALUES,
     "return {scrub_key(key): scrub_attribute(key, value) for", "return {key: scrub_attribute(key, value) for"),
    ("M5", "event attributes not scrubbed", SPAN,
     "Event(redact(event.name), scrubbed_bounded(event.attributes),", "Event(redact(event.name), event.attributes,"),
    ("M6", "event names not scrubbed", SPAN,
     "Event(redact(event.name), scrubbed_bounded", "Event(event.name, scrubbed_bounded"),
    ("M7", "span name not scrubbed", SPAN, "name=redact(span.name),", "name=span.name,"),
    ("M8", "link attributes not scrubbed", SPAN,
     "Link(link.context, scrubbed_bounded(link.attributes))", "Link(link.context, link.attributes)"),
    ("M9", "status description not scrubbed", SPAN,
     "Status(status.status_code, redact(status.description))", "status"),
    ("M10", "numbers never scrubbed", VALUES,
     "    return value if scrubbed == text else scrubbed\n\n\ndef _scrub_any_key",
     "    return value\n\n\ndef _scrub_any_key"),
    ("M11", "mixed sequences not made homogeneous", VALUES,
     "return tuple(str(item) for item in items)", "return items"),
    ("M12", "a failing hook exports the span unscrubbed", PIPE,
     "                _log_failure(hook, \"on_end\", exc)\n                return\n",
     "                _log_failure(hook, \"on_end\", exc)\n                continue\n"),
    ("M13", "instrumentation scope not scrubbed", SPAN,
     "instrumentation_scope=_scrub_scope(span.instrumentation_scope),",
     "instrumentation_scope=span.instrumentation_scope,"),
    ("M14", "resource not scrubbed", SETUP,
     "return Resource(scrub_attributes(resource.attributes), resource.schema_url)", "return resource"),
    ("M15", "dropped-attribute count lost", SPAN,
     'bounded.dropped = getattr(attributes, "dropped", 0) if dropped is None else dropped', "bounded.dropped = 0"),
    # --- values (shared)
    ("M16", "account-like key gives no context", VALUES, "    if is_account_key(key):\n", "    if False:\n"),
    ("M17", "trace-id exemption for any key", VALUES,
     "in _TRACE_ID_KEYS and _W3C_HEX_ID", "in _TRACE_ID_KEYS or _W3C_HEX_ID"),
    ("M18", "nested mappings not scrubbed", VALUES,
     "return {_scrub_any_key(k): scrub_object(str(k), v) for k, v in value.items()}", "return value"),
    ("M19", "other objects (bytes) not scrubbed", VALUES,
     "    return value if scrubbed == text else scrubbed\n\n\ndef _scrub_number",
     "    return value\n\n\ndef _scrub_number"),
    # --- logs
    ("M20", "log message not redacted", LOG,
     "        full = redact(record.getMessage())\n", "        full = record.getMessage()\n"),
    ("M21", "a value split across template and argument passes", LOG,
     "    if redact(rejoined) != rejoined:\n", "    if False:\n"),
    ("M22", "args cleared (breaks uvicorn's access formatter)", LOG,
     "    return template, args\n", "    return full, ()\n"),
    ("M23", "log extras not scrubbed", LOG,
     "fields[scrub_key(name)] = scrub_object(name, value)", "fields[scrub_key(name)] = value"),
    ("M24", "exception object kept on the record", LOG, '    fields["exc_info"] = None\n', ""),
    ("M25", "exception text not redacted", LOG, "    return redact(text) if text else text", "    return text"),
    ("M26", "stack text not redacted", LOG,
     'fields["stack_info"] = redact(record.stack_info) if record.stack_info else record.stack_info',
     'fields["stack_info"] = record.stack_info'),
    ("M27", "makeRecord not patched", LOG,
     "    logging.Logger.makeRecord = scrubbing_make_record  # type: ignore[method-assign]  # see above\n", ""),
    ("M28", "failed scrub emits the raw record", LOG,
     "            return withheld_log_record(record, error)", "            return record"),
    ("M29", "withheld record loses custom fields", LOG,
     "            fields[_withheld_name(name)] = _WITHHELD\n", "            pass\n"),
    ("M30", "failing filter lets the record through", LOG,
     "        except Exception:\n            return False", "        except Exception:\n            return True"),
    ("M31", "filter not added to root handlers", LOG, "        handler.addFilter(log_filter)\n", "        pass\n"),
    ("M32", "the test reset does not restore makeRecord", LOG,
     "            logging.Logger.makeRecord = self.previous_make_record", "            pass"),
    ("M33", "init installs no log redaction", SETUP,
     "        log_redaction = _log_redaction or install_log_redaction()\n",
     "        log_redaction = _log_redaction or install_log_redaction()\n        log_redaction.uninstall()\n"),
    ("M34", "shutdown switches log scrubbing off", SETUP,
     "        finally:\n            with _lock:\n                if _active is self:",
     "        finally:\n            uninstall_log_redaction_for_tests()\n            with _lock:\n                if _active is self:"),
]


PYTEST = ["uv", "run", "--frozen", "pytest", "libs/py-telemetry/tests", "-q", "-p", "no:cacheprovider", "-x"]


def run_mutation(mid: str, what: str, path: Path, old: str, new: str) -> bool:
    original = path.read_text()
    assert original.count(old) == 1, f"{mid}: snippet not found exactly once in {path}"
    path.write_text(original.replace(old, new))
    try:
        result = subprocess.run(PYTEST, capture_output=True, text=True, check=False)
    finally:
        path.write_text(original)
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    killed = result.returncode != 0 and bool(failed)
    verdict = f"killed by {failed[0].split('::')[-1]}" if killed else f"SURVIVED (exit {result.returncode})"
    print(f"{mid} {what} ({path.name}) -> {verdict}")
    return killed


def main() -> int:
    results = [run_mutation(*m) for m in MUTATIONS]
    clean = subprocess.run(["git", "diff", "--quiet", "--", str(SRC)], check=False).returncode == 0
    after = subprocess.run(PYTEST, capture_output=True, text=True, check=False).stdout.strip().splitlines()[-1]
    print(f"{sum(results)}/{len(results)} mutations killed; source restored clean: {clean}; after restore: {after}")
    return 0 if all(results) and clean else 1


if __name__ == "__main__":
    sys.exit(main())
