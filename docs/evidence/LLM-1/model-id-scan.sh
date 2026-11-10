#!/usr/bin/env bash
# LLM-1 box 5: `git grep` for model ids outside config/, .env.example and tests.
# The pattern is the one libs/py-llm/tests/test_no_model_ids_in_code.py enforces in `make check`.
# Run from the repo root: bash docs/evidence/LLM-1/model-id-scan.sh > docs/evidence/LLM-1/model-id-scan.txt
set -u
PATTERN=$(uv run --frozen python -c '
import importlib.util
spec = importlib.util.spec_from_file_location("guard", "libs/py-llm/tests/test_no_model_ids_in_code.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.MODEL_ID_PATTERN)')

echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ) · $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "PATTERN='$PATTERN'"
echo
echo "\$ git grep -nIiE \"\$PATTERN\" -- . ':!config/' ':!.env.example' ':!**/tests/**' ':!**/tests_int/**'"
git grep -nIiE "$PATTERN" -- . ':!config/' ':!.env.example' ':!**/tests/**' ':!**/tests_int/**'
echo "exit=$? (1 = no match)"
echo
echo "# Control: the same pattern over the whole tree, no exclusions (hits are allowed places only)"
echo "\$ git grep -lIiE \"\$PATTERN\""
git grep -lIiE "$PATTERN"
echo "exit=$?"
