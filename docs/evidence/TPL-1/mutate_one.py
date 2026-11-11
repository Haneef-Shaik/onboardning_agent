"""Run one mutation from mutate.py against a pytest -k selection, then restore the file.

    uv run --frozen python docs/evidence/TPL-1/mutate_one.py M23 aadhaar
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mutate", HERE / "mutate.py")
assert spec is not None and spec.loader is not None
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)


def main(mutation_id: str, selection: str) -> int:
    _, guard, rel, old, new = next(m for m in mutate.MUTATIONS if m[0] == mutation_id)
    path = mutate.REPO / rel
    source = path.read_text(encoding="utf-8")
    path.write_text(source.replace(old, new), encoding="utf-8")
    try:
        run = subprocess.run(
            ["uv", "run", "--frozen", "pytest", *mutate.TESTS, "-q", "-p", "no:cacheprovider", "-rf", "-k", selection],
            cwd=mutate.REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        path.write_text(source, encoding="utf-8")
    print(f"{mutation_id} ({guard}) with -k {selection}:")
    print("\n".join(line for line in run.stdout.splitlines() if line.startswith("FAILED") or " passed" in line or " failed" in line))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
