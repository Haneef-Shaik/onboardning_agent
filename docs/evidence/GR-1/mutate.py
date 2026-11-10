"""GR-1 mutation checks: break each guard on purpose, run the py-guardrails suite, record the first failing test.

Run from the repo root on a clean tree: `uv run --frozen python docs/evidence/GR-1/mutate.py`. Every file
is restored after its mutation; the script ends by checking the source tree is clean again.
The ReDoS fix is not mutated here: the old patterns make the suite run for minutes (see
red-review-fixes.txt, where the pre-fix run did not finish in 600 s).
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
    ("M5 tool results treated as trusted", "messages.py",
     '    if isinstance(message, ToolMessage | FunctionMessage):\n        return "tool"',
     '    if isinstance(message, ToolMessage | FunctionMessage):\n        return None'),
    ("M6 input PII redaction off for untrusted string content", "messages.py",
     "        clean = redactor(content)\n", "        clean = content\n"),
    ("M7 input PII redaction off for system messages", "messages.py",
     '            update = {"content": _trusted(message.content, redactor).content, **_fields(message, redact)}',
     "            update = {}"),
    ("M8 untrusted text not delimited", "messages.py",
     "    wrap = partial(delimit_untrusted, source=source)", "    wrap = str"),
    ("M9 delimiter tag inside text not escaped", "delimit.py",
     'escaped = _TAG_IN_TEXT.sub(r"&lt;\\1", text)', "escaped = text"),
    ("M10 output PII scan off for tool-call args", "messages.py",
     '"tool_calls": [{**call, "args": _redact_tree(call["args"], counted)} for call in message.tool_calls],',
     '"tool_calls": message.tool_calls,'),
    ("M11 output PII scan off for content", "messages.py",
     '"content": _redact_ai_content(message.content, counted, plain),', '"content": message.content,'),
    ("M12 no repair: fail on the first invalid reply", "guarded.py",
     "            repair = [*messages, reply, trusted_message(repair_prompt(schema, first))]\n"
     "            second = parse_structured((await self.ainvoke(repair)).text, schema)",
     "            second = first"),
    ("M13 a second repair attempt", "guarded.py",
     '            reason = f"attempts=2; errors={len(second.errors)}; first={second.first_type}"',
     "            second = parse_structured((await self.ainvoke(repair)).text, schema)\n"
     "            if not isinstance(second, SchemaErrors):\n                return second\n"
     '            reason = f"attempts=2; errors={len(second.errors)}; first={second.first_type}"'),
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
     "async with asyncio.timeout(self._budget.remaining_seconds()) as deadline:",
     "async with asyncio.timeout(None) as deadline:"),
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
     "        untrusted.extend(result.redacted)", "        untrusted.extend(result.scanned)"),
    ("M26 trusted instructions delimited like chat", "messages.py",
     '    if isinstance(message, TrustedInstruction):\n        return "instruction"',
     '    if False:\n        return "instruction"'),
    ("M27 trusted instructions not scanned for injection", "messages.py",
     "        if found:\n", '        if found and source != "instruction":\n'),
    ("M28 unknown content blocks pass through untouched", "messages.py",
     '            text = "\\n".join(_leaves(block)) if isinstance(block, dict) else str(block)\n',
     "            blocks.append(block)\n            continue\n"),
    ("M29 structural keys (ids, names) redacted", "messages.py",
     "        return {key: item if key in _STRUCTURAL_KEYS else _redact_tree(item, fn) for key, item in value.items()}",
     "        return {key: _redact_tree(item, fn) for key, item in value.items()}"),
    ("M30 provider timeout reported as a budget block", "guarded.py",
     "            if not deadline.expired():\n", "            if False:\n"),
    ("M31 external guard not bounded by the time left", "guarded.py",
     "            async with asyncio.timeout(self._budget.remaining_seconds()):\n                verdict",
     "            async with asyncio.timeout(None):\n                verdict"),
    ("M32 external output guard skips tool calls", "messages.py",
     "    return tuple(text for text in (message.text, *calls) if text)",
     "    return tuple(text for text in (message.text,) if text)"),
    ("M33 reported input/output token parts ignored", "guarded.py",
     "    return max(total, parts, _estimate(messages, reply))", "    return max(total, _estimate(messages, reply))"),
    ("M34 a failed model call uses no step", "guarded.py",
     "            self._budget.charge(_estimate(messages, None))  # a failed call still used a step", "            pass"),
    ("M35 additional_kwargs of input messages not redacted", "messages.py",
     '    fields: dict[str, Any] = {"additional_kwargs": _redact_tree(message.additional_kwargs, redactor)}',
     "    fields: dict[str, Any] = {}"),
    ("M36 Bedrock metadata keys reported as policies", "external.py",
     '            if not policy.endswith("Policy"):\n', "            if False:\n"),
    ("M37 provider copies of tool calls counted as PII hits", "messages.py",
     "else _redact_tree(block, counted if _is_text_block(block) else plain)",
     "else _redact_tree(block, counted)"),
    ("M38 nothing-to-check recorded as an external pass", "guarded.py",
     "        if guard is None or not texts:", "        if guard is None:"),
]


def purge() -> None:
    for cache in pathlib.Path("libs/py-guardrails").rglob("__pycache__"):
        shutil.rmtree(cache)


def main() -> None:
    lines: list[str] = []
    for label, name, old, new in MUTATIONS:
        path = SRC / name
        original = path.read_text()
        if original.count(old) != 1:
            lines.append(f"{label} -> ANCHOR NOT FOUND ({original.count(old)} matches)")
            print(lines[-1], flush=True)
            continue
        path.write_text(original.replace(old, new))
        purge()
        try:
            result = subprocess.run(
                ["uv", "run", "--frozen", "pytest", "libs/py-guardrails/tests", "-q", "-x", "-p", "no:cacheprovider"],
                capture_output=True, text=True, check=False, timeout=300,
            )
            out, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired:
            out, code = "", -1
        finally:
            path.write_text(original)
            purge()
        failed = [line for line in out.splitlines() if line.startswith("FAILED ")]
        verdict = f"KILLED by {failed[0]}" if code != 0 and failed else f"SURVIVED (rc={code})"
        lines.append(f"{label} -> {verdict}")
        print(lines[-1], flush=True)
    clean = subprocess.run(["git", "diff", "--quiet", "--", str(SRC)], check=False).returncode == 0
    print(f"tree clean after restore: {clean}")
    killed = sum("KILLED" in line for line in lines)
    print(f"{killed}/{len(MUTATIONS)} mutations killed")


if __name__ == "__main__":
    main()
