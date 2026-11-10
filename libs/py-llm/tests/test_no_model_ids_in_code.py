"""No model ids in code (PRD §7.1, MASTER-PLAN §9): they live in `config/`, `.env.example` or tests.

This runs the same `git grep` that LLM-1's Done-when box records, so `make check` keeps enforcing it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# Bedrock ids (`anthropic.claude-…`, `amazon.nova-…`, `meta.llama…`, with optional region prefix),
# and the bare names of the common hosted model families. POSIX ERE only: `git grep -E` on macOS has
# no `\b`, so a word start is spelled `(^|[^a-z0-9_])`.
_START = r"(^|[^a-z0-9_])"
MODEL_ID_PATTERN = (
    rf"{_START}((us|eu|apac|global)\.)?(anthropic|amazon|meta|mistral|cohere|ai21|deepseek|qwen|writer)"
    r"\.[a-z0-9-]+-v?[0-9]"
    rf"|{_START}claude-(instant|[0-9]|opus|sonnet|haiku)[a-z0-9.-]*"
    rf"|{_START}gpt-[0-9o][a-z0-9.-]*"
    rf"|{_START}gemini-[0-9][a-z0-9.-]*"
    rf"|{_START}llama-?[0-9][a-z0-9.-]*"
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


def _git_grep_file(tmp_path: Path, text: str) -> bool:
    """Whether `git grep -E` itself (not Python's `re`) matches `text`, as the repo scan runs it."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    sample = tmp_path / "sample.py"
    sample.write_text(f'MODEL = "{text}"\n', encoding="utf-8")
    result = subprocess.run(
        [git, "grep", "--no-index", "-qIiE", MODEL_ID_PATTERN, "--", sample.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


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
    # Neutral ids: `pytest -v` output lands in evidence files, which the scan covers.
    ids=["bedrock-anthropic", "bedrock-regional", "bedrock-amazon", "bedrock-meta", "claude-bare", "openai", "gemini"],
)
def test_git_grep_catches_real_looking_ids(tmp_path: Path, text: str) -> None:
    assert _git_grep_file(tmp_path, text)


@pytest.mark.parametrize("text", ["langchain-aws", "metadata.version-1", "init_chat_model", "bedrock_converse:x"])
def test_git_grep_ignores_ordinary_names(tmp_path: Path, text: str) -> None:
    assert not _git_grep_file(tmp_path, text)
