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

MUTATIONS: list[tuple[str, str, Path, str, str]] = [
    ("M1", "init installs no scrubber", SETUP,
     "HookedSpanProcessor(export, final_hook=PiiScrubbingSpanProcessor())", "HookedSpanProcessor(export)"),
    ("M2", "scrubber runs before the hooks, not after", PIPE,
     "return self._hooks if final is None else (*self._hooks, final)",
     "return self._hooks if final is None else (final, *self._hooks)"),
    ("M3", "attribute values not scrubbed", SPAN,
     "return {redact(key): scrub_value(value) for", "return {redact(key): value for"),
    ("M4", "attribute keys not scrubbed", SPAN,
     "return {redact(key): scrub_value(value) for", "return {key: scrub_value(value) for"),
    ("M5", "event attributes not scrubbed", SPAN,
     "Event(redact(event.name), scrub_attributes(event.attributes),",
     "Event(redact(event.name), event.attributes,"),
    ("M6", "event names not scrubbed", SPAN,
     "Event(redact(event.name), scrub_attributes", "Event(event.name, scrub_attributes"),
    ("M7", "span name not scrubbed", SPAN, "name=redact(span.name),", "name=span.name,"),
    ("M8", "link attributes not scrubbed", SPAN,
     "Link(link.context, scrub_attributes(link.attributes))", "Link(link.context, link.attributes)"),
    ("M9", "status description not scrubbed", SPAN,
     "Status(status.status_code, redact(status.description))", "status"),
    ("M10", "numbers never scrubbed", SPAN,
     "return value if scrubbed == text else scrubbed", "return value"),
    ("M11", "mixed sequences not made homogeneous", SPAN,
     "return tuple(str(item) for item in items)", "return items"),
    ("M12", "a failing hook exports the span unscrubbed", PIPE,
     "                _log_failure(hook, \"on_end\", exc)\n                return\n",
     "                _log_failure(hook, \"on_end\", exc)\n                continue\n"),
    ("M13", "log message not redacted", LOG, "    return redact(message)\n", "    return message\n"),
    ("M14", "log extras not redacted", LOG, "            fields[name] = redact(value)\n", "            pass\n"),
    ("M15", "exception object kept on the record", LOG, '    fields["exc_info"] = None\n', ""),
    ("M16", "exception text not redacted", LOG, "    return redact(text) if text else text", "    return text"),
    ("M17", "stack text not redacted", LOG,
     'fields["stack_info"] = redact(record.stack_info) if record.stack_info else record.stack_info',
     'fields["stack_info"] = record.stack_info'),
    ("M18", "makeRecord not patched", LOG,
     "    logging.Logger.makeRecord = scrubbing_make_record  # type: ignore[method-assign]  # see above\n", ""),
    ("M19", "failed scrub emits the raw record", LOG,
     "            return withheld_log_record(record, error)", "            return record"),
    ("M20", "failing filter lets the record through", LOG,
     "        except Exception:\n            return False", "        except Exception:\n            return True"),
    ("M21", "filter not added to root handlers", LOG,
     "        handler.addFilter(log_filter)\n", "        pass\n"),
    ("M22", "uninstall does not restore makeRecord", LOG,
     "            logging.Logger.makeRecord = self.previous_make_record", "            pass"),
    ("M23", "init installs no log redaction", SETUP,
     "        log_redaction = install_log_redaction()\n",
     "        log_redaction = install_log_redaction()\n        log_redaction.uninstall()\n"),
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
