"""UI-1 mutation checks: break each guard, run the tests that should catch it, restore the file.

Run from the repo root: `uv run --frozen python docs/evidence/UI-1/mutate.py [ID ...]`.
Each mutation must make the named command fail; the file is restored byte for byte either way.
Output: docs/evidence/UI-1/mutation-checks.txt (with --write).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB_TEST = "node_modules/.bin/vitest run --config apps/web/vitest.config.ts"
UI_TEST = "node_modules/.bin/vitest run --config libs/ts-ui/vitest.config.ts"
E2E = "node_modules/.bin/playwright test --config apps/web/playwright.config.ts"
STATIC = "uv run --frozen pytest tools/tests/test_compose_static.py -q"


@dataclass(frozen=True)
class Mutation:
    id: str
    what: str
    path: str
    old: str
    new: str
    command: str


MUTATIONS = [
    Mutation("M1", "maskedText shows a ptk_ token", "libs/ts-ui/src/masked-field.tsx",
             "if (TOKEN.test(value)) return MASK;", "", UI_TEST),
    Mutation("M2", "MaskedField starts revealed (shows the masked form as 'revealed')", "libs/ts-ui/src/masked-field.tsx",
             'const isRevealed = typeof revealed === "string" && revealed.length > 0;',
             "const isRevealed = true;", UI_TEST),
    Mutation("M3", "a reveal control is shown without onReveal", "libs/ts-ui/src/masked-field.tsx",
             "{!isRevealed && onReveal && (", "{!isRevealed && (", UI_TEST),
    Mutation("M4", "Hires shown to every role, managers included", "apps/web/src/nav/nav.ts",
             'path: "/hires", roles: HR_STAFF', 'path: "/hires", roles: ALL', WEB_TEST),
    Mutation("M5", "Admin shown to HR as well", "apps/web/src/nav/nav.ts",
             'roles: ["HR_ADMIN"], goal: "UI-8"', 'roles: HR_STAFF, goal: "UI-8"', WEB_TEST),
    Mutation("M6", "section match is a bare prefix (/runsx counts as /runs)", "apps/web/src/nav/nav.ts",
             "pathname.startsWith(`${sectionPath}/`)", "pathname.startsWith(sectionPath)", WEB_TEST),
    Mutation("M7", "the route guard ignores the role", "apps/web/src/App.tsx",
             "item && canAccess(me.role, pathname) ?", "item ?", WEB_TEST),
    Mutation("M8", "the session store persists the token to sessionStorage", "apps/web/src/auth/session.ts",
             "current = isLive(session) ? Object.freeze({ ...session }) : null;",
             'current = isLive(session) ? Object.freeze({ ...session }) : null;\n      sessionStorage.setItem("onb.token", session.accessToken);',
             WEB_TEST),
    Mutation("M9", "an expired token still counts as a session", "apps/web/src/auth/session.ts",
             "const isLive = (session: Session) => session.expiresAt * 1000 > nowMs();",
             "const isLive = (_session: Session) => true;", WEB_TEST),
    Mutation("M10", "a 401 to a signed-in call does not end the session", "apps/web/src/api.ts",
             "if (response.status === 401 && sent !== null && current !== null && sent === bearer(current)) {",
             "if (response.status === 999 && sent !== null && current !== null && sent === bearer(current)) {", WEB_TEST),
    Mutation("M11", "a wrong password at login counts as a lost session", "apps/web/src/api.ts",
             "if (response.status === 401 && sent !== null && current !== null && sent === bearer(current)) {",
             "if (response.status === 401) {", WEB_TEST),
    Mutation("M12", "the client never sends the bearer token", "apps/web/src/api.ts",
             "request.headers.set(AUTHORIZATION, bearer(current));", "", WEB_TEST),
    Mutation("M13", "a non-API error's text is shown to the user", "apps/web/src/errors.ts",
             "return { message: GENERIC, fields: [], traceId: null };",
             "return { message: error instanceof Error ? error.message : GENERIC, fields: [], traceId: null };",
             WEB_TEST),
    Mutation("M14", "login failure shows the session-ended text", "apps/web/src/errors.ts",
             'return context === "login" ? "Email or password is incorrect." : "Your session has ended. Sign in again.";',
             'return "Your session has ended. Sign in again.";', WEB_TEST),
    Mutation("M15", "Caddy forwards /api without stripping the prefix", "infra/caddy/Caddyfile.dev",
             "handle_path /api/* {", "handle /api/* {", STATIC),
    Mutation("M16", "Playwright: managers see Hires (end to end)", "apps/web/src/nav/nav.ts",
             'path: "/hires", roles: HR_STAFF', 'path: "/hires", roles: ALL', E2E),
    Mutation("M17", "a token passed as the revealed value is shown", "libs/ts-ui/src/masked-field.tsx",
             "return TOKEN.test(revealed) ? MASK : revealed;", "return revealed;", UI_TEST),
    Mutation("M18", "a late 401 for an older token ends the new session", "apps/web/src/api.ts",
             "sent !== null && current !== null && sent === bearer(current)", "sent !== null", WEB_TEST),
    Mutation("M19", "a backslash return path is accepted after login", "apps/web/src/nav/nav.ts",
             r'if (from.startsWith("//") || from.includes("\\")) return null;',
             'if (from.startsWith("//")) return null;', WEB_TEST),
    Mutation("M20", "the app is served over plain HTTP too", "infra/caddy/Caddyfile.dev",
             "https://localhost {\n\thandle /healthz", "http://localhost, https://localhost {\n\thandle /healthz", STATIC),
    Mutation("M21", "a login token already expired by this clock is accepted silently", "apps/web/src/api.ts",
             "if (token.expires_at * 1000 <= Date.now()) throw new ClockSkewError();", "", WEB_TEST),
]

# vitest "FAIL  file > suite > test", pytest "FAILED file::test", Playwright "1) [chromium] › file › test".
FAIL_LINE = re.compile(r"^\s*(?:FAIL\s|FAILED\s|\d+\) \[chromium\]).*$", re.M)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(m: Mutation) -> str:
    path = ROOT / m.path
    original = path.read_bytes()
    text = original.decode()
    if text.count(m.old) != 1:
        return f"{m.id}: SETUP ERROR — pattern not found once in {m.path}"
    try:
        path.write_text(text.replace(m.old, m.new))
        result = subprocess.run(m.command, shell=True, cwd=ROOT, capture_output=True, text=True, check=False)
    finally:
        path.write_bytes(original)
    restored = path.read_bytes() == original
    output = ANSI.sub("", result.stdout + result.stderr)
    lines = sorted({line.strip() for line in FAIL_LINE.findall(output)})[:6]
    verdict = "CAUGHT" if result.returncode != 0 else "MISSED"
    detail = "\n    ".join(lines) if lines else "(no failure lines)"
    return f"{m.id} {verdict} (exit={result.returncode}, restored byte-for-byte: {restored}) — {m.what}\n  $ {m.command}\n    {detail}"


def main(argv: list[str]) -> int:
    write = "--write" in argv
    wanted = {a for a in argv if not a.startswith("--")}
    chosen = [m for m in MUTATIONS if not wanted or m.id in wanted]
    reports = [run(m) for m in chosen]
    caught = sum(" CAUGHT " in r for r in reports)
    summary = f"SUMMARY: {caught} of {len(chosen)} mutations caught"
    text = "\n\n".join(reports) + f"\n\n{summary}\n"
    print(text)
    if write:
        (ROOT / "docs/evidence/UI-1/mutation-checks.txt").write_text(text)
    return 0 if caught == len(chosen) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
