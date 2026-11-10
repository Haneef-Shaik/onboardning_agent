"""Mutation checks for the bank keyword fix: break each part, run the PII tests, restore."""

import os
import pathlib
import shutil
import subprocess

SRC = pathlib.Path("libs/py-guardrails/src/onb_guardrails/pii/detectors.py")
TESTS = "libs/py-guardrails/tests/pii"
KW_LINE = '    rf"(?:a/c|acc(?:oun)?t|acc)s?(?:{_KW_GAP}{{0,3}}(?:no|number|num))?"\n'
IS_LINE = '    rf"(?:{_KW_GAP}{{1,3}}is)?{_KW_GAP}{{0,6}}"\n'
GAP = '_KW_GAP: Final = r"""[\\s_\\-:=#."\']"""'
ORIGINAL_PATTERN = (
    '    r"\\b(?:a/c|acc(?:oun)?t|acc)\\b\\.?"\n'
    '    r"(?:\\s{0,3}(?:no|number|num)\\b\\.?|\\s{0,3}\\#)?(?:\\s{1,3}is)?\\s{0,3}[:#\\-]?\\s{0,3}"\n'
)
MUTATIONS = [
    ("F1 \\b before the keyword", '    rf"(?:a/c|acc', '    rf"\\b(?:a/c|acc'),
    ("F2 gaps whitespace only", GAP, '_KW_GAP: Final = r"\\s"'),
    ("F3 gaps without punctuation (only [\\s_])", GAP, '_KW_GAP: Final = r"[\\s_]"'),
    ("F4 keyword end rejects digits/_ (\\b-like)", "(?:no|number|num))?\"\n", "(?:no|number|num))?(?![0-9_])\"\n"),
    ("F5 no/number must be separated", "{_KW_GAP}{{0,3}}(?:no|number|num)", "{_KW_GAP}{{1,3}}(?:no|number|num)"),
    ("F6 plural keyword dropped", "|acc)s?(", "|acc)("),
    ("F7 letters allowed after keyword (accept matches)", "(?:no|number|num))?\"\n", "(?:no|number|num))?[a-z]{0,6}\"\n"),
    ("F8 whole pattern reverted to main", KW_LINE + IS_LINE, ORIGINAL_PATTERN),
]
original = SRC.read_text()
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def purge() -> None:
    for cache in pathlib.Path("libs/py-guardrails").rglob("__pycache__"):
        shutil.rmtree(cache)


def run_tests() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--frozen", "pytest", TESTS, "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, check=False,
    )


try:
    for name, old, new in MUTATIONS:
        assert original.count(old) == 1, f"{name}: anchor not unique"
        SRC.write_text(original.replace(old, new))
        purge()
        run = run_tests()
        failed = [line for line in run.stdout.splitlines() if line.startswith("FAILED")]
        errors = [line for line in run.stdout.splitlines() if line.startswith("ERROR")]
        status = "KILLED" if failed else ("BROKEN (errors, not a valid kill)" if run.returncode else "SURVIVED")
        print(f"{name}: {status} ({len(failed)} failing)")
        for line in (failed or errors)[:3]:
            print("   ", line[:200])
finally:
    SRC.write_text(original)
    purge()
print("after restore:", run_tests().stdout.strip().splitlines()[-1])
print("source restored byte-identical:", SRC.read_text() == original)
