"""HUB-1 mutation checks: break each guard on purpose, run its tests, record which named test failed,
restore the file byte for byte. Run from the repo root: `uv run --frozen python docs/evidence/HUB-1/mutate.py`.
`--int` also runs the mutations checked against the integration and cross-tenant suites (Docker)."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HUB = "apps/mcp-hub/src/onb_mcp_hub/"
TOKENS = "libs/py-agents/src/onb_agents/tokens.py"
HUB_UNIT = ["apps/mcp-hub/tests"]
TOKEN_UNIT = ["libs/py-agents/tests/test_tokens.py"]
HUB_INT = ["apps/mcp-hub/tests_int"]
EVALS = ["libs/py-evals/tests/test_hub_audit.py"]


@dataclass(frozen=True)
class Mutation:
    id: str
    guard: str
    path: str
    old: str
    new: str
    tests: list[str]


M = Mutation
MUTATIONS = [
    M("M1", "token expiry", TOKENS, "return _verified(_decode(token, key, verify_exp=True))",
      "return _verified(_decode(token, key, verify_exp=False))", TOKEN_UNIT + HUB_UNIT),
    M("M2", "token lifetime <= 15 min", TOKENS, "not 0 < expires - issued <= MAX_LIFETIME_S", "not 0 < expires - issued",
      TOKEN_UNIT),
    M("M3", "expired-token claims kept for audit", TOKENS,
      "return _verified(_decode(token, key, verify_exp=False)).claims", "return None", TOKEN_UNIT + HUB_UNIT),
    M("M4", "vault hard deny in arguments", HUB + "policy.py", "    _check_vault(args)\n", "", HUB_UNIT),
    M("M5", "forbidden collections dropped from the context", HUB + "policy.py",
      "if n in provisioned and not _forbidden(n)", "if n in provisioned or _forbidden(n)", HUB_UNIT),
    M("M6", "tool must be in the manifest", HUB + "policy.py", "    if tool.tool not in grant.tools:", "    if False:",
      HUB_UNIT),
    M("M7", "tool must be in the token scopes", HUB + "policy.py", "    if tool.name not in token.scopes:",
      "    if False:", HUB_UNIT),
    M("M8", "collection must be in the manifest", HUB + "policy.py", "        if collection not in allowed_listed:",
      "        if False:", HUB_UNIT),
    M("M9", "collection must be provisioned", HUB + "policy.py", "        if collection not in provisioned:",
      "        if False:", HUB_UNIT),
    M("M10", "JSON-schema contract", HUB + "policy.py", "    if errors:\n", "    if False:\n", HUB_UNIT),
    M("M11", "org_id never from arguments", HUB + "policy.py",
      'and "org_id" in text.lstrip("$").split(".")', 'and text == "never"', HUB_UNIT),
    M("M12", "no raw PII in arguments", HUB + "policy.py", "    _check_pii(args)\n", "", HUB_UNIT),
    M("M13", "requires_approval surfaced", HUB + "policy.py",
      'if rule in candidates and f"{APPROVAL_SCOPE_PREFIX}{rule}" not in token.scopes:', "if False:", HUB_UNIT),
    M("M14", "agent record must be the token's agent", HUB + "policy.py",
      "if agent is None or agent.name != token.agent or agent.manifest.name != token.agent:", "if agent is None:",
      HUB_UNIT),
    M("M15", "retired agent grants nothing", HUB + "policy.py", "    if not agent.active:", "    if False:", HUB_UNIT),
    M("M16", "tenant scope from the token", HUB + "gateway.py", "            org_id=claims.org_id,\n",
      '            org_id="org_globex",\n', HUB_UNIT),
    M("M17", "credential block in responses", HUB + "scan.py", "        if any(secret in value for secret in self._secrets):",
      "        if False:", HUB_UNIT),
    M("M18", "PII redaction of responses", HUB + "scan.py", "        found = count_pii(value, key)", "        found = 0",
      HUB_UNIT),
    M("M19", "numbers scanned for PII", HUB + "scan.py", "        if number is not None and count_pii(number):",
      "        if False:", HUB_UNIT),
    M("M20", "hex-id exemption only under id keys", HUB + "pii.py",
      "(_id_key(key) and _HEX_ID.fullmatch(text) is not None)", "(_HEX_ID.fullmatch(text) is not None)", HUB_UNIT),
    M("M21", "this call's signed context is a secret", HUB + "gateway.py", "secrets=(*self.secrets, *sent)",
      "secrets=self.secrets", HUB_UNIT),
    M("M22", "every denial is audited", HUB + "gateway.py",
      "            await self._audit(call, AuditOutcome.FAILED if ran else AuditOutcome.DENIED, denied.reason)\n",
      "            pass\n", HUB_UNIT),
    M("M23", "every denial has an outcome=denied span", HUB + "gateway.py",
      'span.set_attributes({"outcome": "denied", "denial.reason": denied.reason})',
      'span.set_attributes({"denial.reason": denied.reason})', HUB_UNIT),
    M("M24", "allowed call's audit is fail-closed", HUB + "gateway.py",
      '            _log.error("mcp-hub audit failed for an allowed call: %s", type(exc).__name__)\n'
      '            return _error(f"{tool.name}: internal error")',
      '            _log.error("mcp-hub audit failed for an allowed call: %s", type(exc).__name__)\n'
      "            return scanned.result", HUB_UNIT),
    M("M25", "unexpected failures audited, never echoed", HUB + "gateway.py",
      "            except Exception as exc:  # a registry or policy failure",
      "            except ZeroDivisionError as exc:  # a registry or policy failure", HUB_UNIT),
    M("M26", "no agent text in audit targets", HUB + "gateway.py",
      'return value if recordable_collection(value) else "other"', "return value", HUB_UNIT),
    M("M27", "agent token reaches the gateway from the header", HUB + "server.py",
      'authorization = headers.get("authorization") if headers is not None else None', "authorization = None",
      HUB_UNIT),
    M("M28", "DNS-rebinding protection", HUB + "server.py", "enable_dns_rebinding_protection=True",
      "enable_dns_rebinding_protection=False", HUB_UNIT),
    M("M29", "eval runner reads hub records", "libs/py-evals/src/onb_evals/toolbox.py",
      "        if doc.get(\"action\") == HUB_AUDIT_ACTION:", "        if False:", EVALS),
]
INT_MUTATIONS = [
    M("M30", "tenant scope from the token (real stack)", HUB + "gateway.py", "            org_id=claims.org_id,\n",
      '            org_id="org_globex",\n', HUB_INT),
    M("M31", "vault hard deny (real stack)", HUB + "policy.py", "    _check_vault(args)\n", "", HUB_INT),
    M("M32", "PII redaction (real stack)", HUB + "scan.py", "        found = count_pii(value, key)", "        found = 0",
      HUB_INT),
    M("M33", "credential block (real stack)", HUB + "scan.py",
      "        if any(secret in value for secret in self._secrets):", "        if False:", HUB_INT),
]


def run(mutation: Mutation) -> tuple[bool, list[str]]:
    path = ROOT / mutation.path
    original = path.read_bytes()
    text = original.decode()
    if text.count(mutation.old) != 1:
        raise SystemExit(f"{mutation.id}: the guard text is not unique in {mutation.path}")
    path.write_text(text.replace(mutation.old, mutation.new))
    try:
        failed: list[str] = []
        for target in mutation.tests:
            proc = subprocess.run(  # noqa: S603
                ["uv", "run", "--frozen", "pytest", target, "-q", "-p", "no:cacheprovider", "-x", "--no-header"],  # noqa: S607
                cwd=ROOT, capture_output=True, text=True, timeout=900, check=False,
            )
            failed += re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.MULTILINE)
        return bool(failed), failed
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original


def main() -> None:
    mutations = MUTATIONS + (INT_MUTATIONS if "--int" in sys.argv else [])
    caught = 0
    for mutation in mutations:
        killed, failed = run(mutation)
        caught += killed
        status = "CAUGHT" if killed else "SURVIVED"
        print(f"{mutation.id} {status}: {mutation.guard} ({mutation.path})")
        for name in failed[:3]:
            print(f"    FAILED {name}")
    print(f"SUMMARY: {caught} of {len(mutations)} mutations caught")


if __name__ == "__main__":
    main()
