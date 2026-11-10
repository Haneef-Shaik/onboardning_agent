"""No model ids in code (PRD §7.1, MASTER-PLAN §9): they live in `config/`, `.env.example` or tests.

This runs the same `git grep` that LLM-1's Done-when box records, so `make check` keeps enforcing it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# Bedrock ids (`anthropic.claude-…`, `amazon.nova-…`, `meta.llama…`, with optional region prefix),
# and the bare names of the common hosted model families.
MODEL_ID_PATTERN = (
    r"\b((us|eu|apac|global)\.)?(anthropic|amazon|meta|mistral|cohere|ai21|deepseek|qwen|writer)"
    r"\.[a-z0-9-]+-v?[0-9]"
    r"|\bclaude-(instant|[0-9]|3|opus|sonnet|haiku)[a-z0-9.-]*"
    r"|\bgpt-[0-9o][a-z0-9.-]*"
    r"|\bgemini-[0-9][a-z0-9.-]*"
    r"|\bllama-?[0-9][a-z0-9.-]*"
)

# The only places model ids may appear (LLM-1 card): config, the env template and tests.
ALLOWED = (":!config/", ":!.env.example", ":!**/tests/**", ":!**/tests_int/**")


def model_id_hits() -> list[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        [git, "grep", "-nIiE", MODEL_ID_PATTERN, "--", ".", *ALLOWED],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr  # 1 means "no match"
    return result.stdout.splitlines()


def test_no_model_ids_outside_config_env_example_and_tests() -> None:
    assert model_id_hits() == []


@pytest.mark.parametrize(
    "text",
    [
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "amazon.nova-pro-v1:0",
        "meta.llama3-1-70b-instruct-v1:0",
        "claude-opus-4-1",
        "gpt-4o-mini",
        "gemini-2.5-pro",
    ],
)
def test_the_pattern_catches_real_looking_ids(text: str) -> None:
    import re

    assert re.search(MODEL_ID_PATTERN, text, flags=re.IGNORECASE)
