"""Mutation checks for the review-fix guards. Each mutation is a list of (old, new) edits in one file."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
M = "libs/py-agents/src/onb_agents/registry/manifest.py"
S = "libs/py-agents/src/onb_agents/registry/sync.py"
T = "libs/py-agents/tests/test_review_fixes.py"
MUTATIONS = [
    ("M16 binary: strict strings, bytes scan and post-parse scan all off", M, [
        ("Identifier = Annotated[StrictStr,", "Identifier = Annotated[str,"),
        ("        yield bytes(value).decode(\"utf-8\", errors=\"replace\")", "        yield \"\""),
        ("        named = sorted({s for s in _strings(self.document()) if names_a_forbidden_collection(s)})",
         "        named: list[str] = []"),
    ]),
    ("M4 users grantable: added to the read allowlist and dropped from the never-grantable set", M, [
        ('        "messages",\n    }\n)', '        "messages",\n        "users",\n    }\n)'),
        ('_NEVER_GRANTABLE = frozenset({"users"}) | GLOBAL_COLLECTIONS', "_NEVER_GRANTABLE = GLOBAL_COLLECTIONS"),
    ]),
    ("M17 strict strings only off (other layers hold)", M, [
        ("Identifier = Annotated[StrictStr,", "Identifier = Annotated[str,"),
    ]),
    ("M18 vault match back to prefix", M, [
        ("any(name in word for word in", "any(word.startswith(name) for word in"),
    ]),
    ("M19 read allowlist off", M, [
        ("sorted((set(value) - AGENT_READABLE_COLLECTIONS) | (set(value) & _NEVER_GRANTABLE))",
         "sorted(set(value) & _NEVER_GRANTABLE)"),
    ]),
    ("M20 write allowlist off", M, [
        ("        if not_writable:\n", "        if False:\n"),
    ]),
    ("M21 params max_tokens may exceed limits", M, [
        ("        if max_tokens is not None and max_tokens > self.limits.max_tokens:",
         "        if False:"),
    ]),
    ("M22 stored hash not checked on read", S, [
        ("        if manifest.name != name or manifest.sha256() != document[\"sha256\"]:", "        if False:"),
    ]),
    ("M23 lost insert race ignored", S, [
        ("            stored = await self._store.insert_agent(write.document)",
         "            await self._store.insert_agent(write.document)\n            stored = True"),
    ]),
]

for label, rel, edits in MUTATIONS:
    path = ROOT / rel
    original = path.read_text()
    mutated = original
    for old, new in edits:
        assert mutated.count(old) == 1, (label, old)
        mutated = mutated.replace(old, new)
    path.write_text(mutated)
    try:
        result = subprocess.run(
            ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", T, "libs/py-agents/tests/test_manifest.py"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
    finally:
        path.write_text(original)
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    summary = result.stdout.strip().splitlines()[-1]
    print(f"{label}: {'CAUGHT' if result.returncode else 'SURVIVED'} · {summary}")
    for name in failed[:3]:
        print(f"    {name.split('::', 1)[1]}")
