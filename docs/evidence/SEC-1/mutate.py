"""SEC-1 mutation checks: plant a leak (or break a guard), run the suite that must catch it, record which test
failed and the findings it printed, restore the source. Run from the repo root with the compose stack up:

    uv run --frozen python docs/evidence/SEC-1/mutate.py [ID ...] > docs/evidence/SEC-1/mutation-checks.txt

P*: production leaks `make test-pii`'s scan must catch (box 2). C1: a control, the same leak as P4 with the
log scrubber left on: the scrubber removes it and the scan is clean (defense in depth, expected to pass).
S*: the scanner's and tamper report's own guards, caught by their unit tests. T1: a broken vault signature
check, caught by the tamper report (box 3). Every source file is restored and checked with `git diff`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UV = ["uv", "run", "--frozen", "--env-file", ".env", "pytest", "-q", "-s", "-p", "no:cacheprovider"]
SCAN = [*UV, "tools/tests_pii/test_pii_scan.py"]
TAMPER = [*UV, "tools/tests_pii/test_tamper_report.py"]
UNIT = [*UV, "tools/tests/test_pii_scanner.py", "tools/tests/test_tamper_manifest.py"]

HIRES = "apps/api/src/onb_api/hires/service.py"
NOTES = '"notes": redact(request.notes),'
LOG_LEAK = '"notes": redact(request.notes) if log.warning("hire email %s", request.pii.email) is None else "",'
SPAN_LEAK = (
    '"notes": redact(request.notes) if __import__("opentelemetry.trace").trace.get_current_span()'
    '.set_attribute("hire.email", request.pii.email) is None else "",'
)


@dataclass(frozen=True)
class Mutant:
    id: str
    what: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old, new)
    command: list[str]
    expect_fail: bool = True


MUTANTS = (
    Mutant(
        "P1",
        "mock_outbox keeps the Telegram chat id (the raw mobile) next to the token",
        (
            (
                "apps/mcp-mocks/src/onb_mcp_mocks/notify/outbox.py",
                '"recipient": {"token": entry.recipient_token, "masked": entry.recipient_masked},',
                '"recipient": {"token": entry.recipient_token, "masked": entry.recipient_masked, "chat": chat_id},',
            ),
        ),
        SCAN,
    ),
    Mutant("P2", "hire notes stored without redaction", ((HIRES, NOTES, '"notes": request.notes,'),), SCAN),
    Mutant(
        "P3",
        "the chat question stored and sent to the model without redaction",
        (("apps/api/src/onb_api/chat/service.py", "question = redact(message)", "question = message"),),
        SCAN,
    ),
    Mutant(
        "P4",
        "the hire service logs the raw email, and the OBS-1 log scrubber is off",
        (
            (HIRES, NOTES, LOG_LEAK),
            (
                "libs/py-telemetry/src/onb_telemetry/log_scrub.py",
                'fields["msg"], fields["args"] = _scrubbed_message(record)',
                'fields["msg"], fields["args"] = record.msg, record.args',
            ),
        ),
        SCAN,
    ),
    Mutant(
        "P5",
        "the hire service puts the raw email on its span, and the OBS-1 span scrubber passes attributes through",
        (
            (HIRES, NOTES, SPAN_LEAK),
            (
                "libs/py-telemetry/src/onb_telemetry/span_scrub.py",
                "attributes=scrubbed_bounded(span.attributes, span.dropped_attributes),",
                "attributes=span.attributes,",
            ),
        ),
        SCAN,
    ),
    Mutant(
        "C1",
        "control: the hire service logs the raw email, scrubber on (expected clean: the scrubber removes it)",
        ((HIRES, NOTES, LOG_LEAK),),
        SCAN,
        expect_fail=False,
    ),
    Mutant(
        "P6",
        "the scan skips mock_outbox",
        (("tools/tests_pii/collect.py", "if name in VAULT_COLLECTIONS:", 'if name in VAULT_COLLECTIONS | {"mock_outbox"}:'),),
        SCAN,
    ),
    Mutant("S1", "numbers matched only verbatim", (("tools/tests_pii/scan.py", '_DIGIT_GAP: Final = r"[^0-9A-Za-z]{0,3}"', '_DIGIT_GAP: Final = r""'),), UNIT),
    Mutant(
        "S2",
        "every detector hit excused as an id",
        (("tools/tests_pii/scan.py", '    """Whether `text[start:end]` lies wholly inside one opaque id (see the module doc)."""\n',
          '    """Whether `text[start:end]` lies wholly inside one opaque id (see the module doc)."""\n    return True\n'),),
        UNIT,
    ),
    Mutant("S3", "an allow rule ignores its location", (("tools/tests_pii/scan.py", "and re.fullmatch(self.location, hit.location) is not None", "and True"),), UNIT),
    Mutant("S4", "document keys not scanned", (("tools/tests_pii/scan.py", 'yield Item(source, f"{location}.<key>", name)', "pass"),), UNIT),
    Mutant("S5", "the tamper guard lets unlisted tamper tests through", (("tools/tests_pii/tamper.py", "if GUARD.search(node.split", "if False and GUARD.search(node.split"),), UNIT),
    Mutant("S6", "a skipped tamper case counts as rejected", (("tools/tests_pii/tamper.py", '{"failure", "error", "skipped"}', '{"failure", "error"}'),), UNIT),
    Mutant(
        "T1",
        "the vault no longer checks a record's signature",
        (("libs/py-crypto/src/onb_crypto/vault.py", "if not hmac.compare_digest(record.sig, await self._signature(record)):", "if False:"),),
        TAMPER,
    ),
)  # fmt: skip

SHOWN = re.compile(r"^(E\s+.*(raw:|detector:|AssertionError|not_rejected|assert ).*|FAILED .*|SEC-1 .*|\d+ (passed|failed).*)$")


def _apply(mutant: Mutant) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for file, old, new in mutant.edits:
        path = ROOT / file
        text = originals.get(path) or path.read_text(encoding="utf-8")
        originals.setdefault(path, text)
        current = path.read_text(encoding="utf-8")
        if current.count(old) != 1:
            raise SystemExit(f"{mutant.id}: pattern not found exactly once in {file}")
        path.write_text(current.replace(old, new), encoding="utf-8")
    return originals


def run(mutant: Mutant) -> bool:
    originals = _apply(mutant)
    try:
        done = subprocess.run(mutant.command, cwd=ROOT, capture_output=True, text=True, check=False)
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
    failed = done.returncode != 0
    ok = failed == mutant.expect_fail
    verdict = ("KILLED" if failed else "SURVIVED") if mutant.expect_fail else ("CLEAN (as expected)" if not failed else "UNEXPECTED FAILURE")
    print(f"=== {mutant.id} {verdict}: {mutant.what}")
    print(f"    command: {' '.join(mutant.command[5:])}  (exit {done.returncode})")
    lines = [line for line in done.stdout.splitlines() if SHOWN.match(line)]
    for line in lines[:12]:
        print(f"    {line[:300]}")
    return ok


def main(selected: list[str]) -> int:
    chosen = [m for m in MUTANTS if not selected or m.id in selected]
    results = {m.id: run(m) for m in chosen}
    diff = subprocess.run(["git", "diff", "--stat"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    print(f"\nsource diff after all mutations: {diff.strip() or '0 files differ'}")
    bad = [mid for mid, ok in results.items() if not ok]
    print(f"result: {len(results) - len(bad)}/{len(results)} as expected; unexpected: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
