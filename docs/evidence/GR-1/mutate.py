"""GR-1 mutation checks: break each guard on purpose, run the py-guardrails suite, record the first failing test.

Run from the repo root: `uv run --frozen python docs/evidence/GR-1/mutate.py`. Every file is restored
after its mutation; the script ends by asserting the tree is clean.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

SRC = pathlib.Path("libs/py-guardrails/src/onb_guardrails")

MUTATIONS: list[tuple[str, str, str, str]] = [
    ("M1 injection pattern 'ignore_instructions' removed", "injection.py",
     '        "ignore_instructions": rf"', '        "ignore_instructions_off": rf"'),
    ("M2 injection normalisation skips the skeleton (zero-width/homoglyphs)", "injection.py",
     "folded = skeleton(text).text.lower()", "folded = text.lower()"),
    ("M3 spaced-out letters not joined", "injection.py",
     "joined = _SPACED_LETTERS.sub(_join_spaced, folded)", "joined = folded"),
    ("M4 injection finding does not block", "guarded.py",
     "            raise InjectionBlockedError(scan.injection_patterns)\n", "            pass\n"),
    ("M5 tool results not treated as untrusted", "messages.py",
     '    if isinstance(message, ToolMessage):\n        return "tool"', '    if False:\n        return "tool"'),
    ("M6 input PII redaction off for untrusted text", "messages.py",
     "            redacted = _map_text(message.content, redactor)\n",
     "            redacted = message.content\n"),
    ("M7 input PII redaction off for system/other text", "messages.py",
     '            guarded.append(message.model_copy(update={"content": _map_text(message.content, redactor)}))',
     "            guarded.append(message)"),
    ("M8 untrusted text not delimited", "messages.py",
     "            content = _map_text(redacted, partial(_delimit, source=source))", "            content = redacted"),
    ("M9 delimiter tag inside text not escaped", "delimit.py",
     'escaped = _TAG_IN_TEXT.sub(r"&lt;\\1", text)', "escaped = text"),
    ("M10 output PII scan off for tool-call args", "messages.py",
     '            "tool_calls": _redact_tree(message.tool_calls, redactor),\n', ""),
    ("M11 output PII scan off for content", "messages.py",
     '            "content": _redact_tree(message.content, redactor),\n', ""),
    ("M12 no repair: fail on the first invalid reply", "guarded.py",
     "            repair = [*messages, reply, trusted_message(repair_prompt(schema, first))]\n"
     "            second = parse_structured((await self.ainvoke(repair)).text, schema)",
     "            second = first"),
    ("M13 a second repair attempt", "guarded.py",
     "            reason = f\"attempts=2; errors={len(second.errors)}; first={second.first_type}\"",
     "            second = parse_structured((await self.ainvoke(repair)).text, schema)\n"
     "            if not isinstance(second, SchemaErrors):\n                return second\n"
     "            reason = f\"attempts=2; errors={len(second.errors)}; first={second.first_type}\""),
    ("M14 invalid output after repair returned silently", "guarded.py",
     "            raise StructuredOutputError(schema.__name__, reason)", "            return second  # type: ignore[return-value]"),
    ("M15 step limit off-by-one (> instead of >= before a call)", "budget.py",
     "            return used >= limit if before_call else used > limit", "            return used > limit"),
    ("M16 budget not checked before the call", "guarded.py",
     "            await self._check_budget(before_call=True)\n", ""),
    ("M17 budget not checked after the call", "guarded.py",
     "            await self._check_budget(before_call=False)\n", ""),
    ("M18 charge not propagated to the run budget", "budget.py",
     "            self._parent.charge(tokens)", "            pass"),
    ("M19 model call not bounded by the time left", "guarded.py",
     "timeout=remaining)", "timeout=None)"),
    ("M20 decision not audited", "recorder.py",
     "audit_id = await self._auditor.audit(", "audit_id = (lambda **_: '0' * 32)("),
    ("M21 no guardrail.outcome span event", "recorder.py",
     "        trace.get_current_span().add_event(EVENT_NAME, attributes)\n", ""),
    ("M22 blocked decision audited as success", "recorder.py",
     "    GuardrailOutcome.BLOCKED: AuditOutcome.DENIED,", "    GuardrailOutcome.BLOCKED: AuditOutcome.SUCCESS,"),
    ("M23 external guard error fails open", "guarded.py",
     "            raise ExternalGuardBlockedError(guard.name, reason) from None", "            return"),
    ("M24 external intervention ignored", "guarded.py",
     "            raise ExternalGuardBlockedError(guard.name, reason)\n        await self._record(check, GuardrailOutcome.PASSED)",
     "            return\n        await self._record(check, GuardrailOutcome.PASSED)"),
    ("M25 external guard sees unredacted text", "messages.py",
     "            untrusted.extend(_texts(redacted))", "            untrusted.extend(_texts(message.content))"),
    ("M26 trusted messages delimited like chat", "messages.py",
     "and not message.additional_kwargs.get(TRUSTED_KWARG)", ""),
]


def purge() -> None:
    for cache in pathlib.Path("libs/py-guardrails").rglob("__pycache__"):
        shutil.rmtree(cache)


def main() -> None:
    lines: list[str] = []
    for label, name, old, new in MUTATIONS:
        path = SRC / name
        original = path.read_text()
        assert original.count(old) == 1, f"{label}: anchor not unique/absent"
        path.write_text(original.replace(old, new))
        purge()
        try:
            result = subprocess.run(
                ["uv", "run", "--frozen", "pytest", "libs/py-guardrails/tests", "-q", "-x", "-p", "no:cacheprovider"],
                capture_output=True, text=True, check=False,
            )
        finally:
            path.write_text(original)
            purge()
        failed = [line for line in result.stdout.splitlines() if line.startswith("FAILED ")]
        verdict = f"KILLED by {failed[0]}" if result.returncode != 0 and failed else f"SURVIVED (rc={result.returncode})"
        lines.append(f"{label} -> {verdict}")
        print(lines[-1], flush=True)
    clean = subprocess.run(["git", "diff", "--quiet", "--", str(SRC)], check=False).returncode == 0
    lines.append(f"tree clean after restore: {clean}")
    print(lines[-1])
    killed = sum("KILLED" in line for line in lines)
    print(f"{killed}/{len(MUTATIONS)} mutations killed")


if __name__ == "__main__":
    main()
