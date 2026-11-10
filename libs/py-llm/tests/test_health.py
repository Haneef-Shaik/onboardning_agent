"""`python -m onb_llm.health --connector X` (run by `make llm-health CONNECTOR=X`)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from onb_llm.health import main

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "llm.yaml"
MODEL_ENV = {"LLM_MODEL_FAST": "model-f", "LLM_MODEL_STANDARD": "model-s", "LLM_MODEL_DEEP": "model-d"}


def test_checks_every_alias_served_by_the_connector(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--connector", "fake", "--config", str(REPO_CONFIG)], env={**MODEL_ENV, "LLM_CONNECTOR": "fake"})
    out = capsys.readouterr().out
    assert code == 0
    for alias, model in (("fast", "model-f"), ("standard", "model-s"), ("deep", "model-d")):
        assert f"alias={alias} connector=fake model={model} ok" in out
    assert "3 of 3 healthy" in out


def test_fails_when_no_alias_uses_the_connector(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--connector", "fake", "--config", str(REPO_CONFIG)], env={**MODEL_ENV, "AWS_REGION": "x"})
    assert code == 2
    assert "no alias uses connector 'fake'" in capsys.readouterr().err


def test_reports_an_unhealthy_alias_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "llm.yaml"
    path.write_text(
        "default: { alias: a }\n"
        "aliases:\n"
        "  a: { connector: fake, model: m }\n"
        "  b: { connector: langchain, model: no-provider }\n",
        encoding="utf-8",
    )
    assert main(["--connector", "langchain", "--config", str(path)], env={}) == 1
    assert "alias=b connector=langchain model=no-provider FAILED LLMConfigError" in capsys.readouterr().out


def test_a_config_error_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--connector", "fake", "--config", str(REPO_CONFIG)], env={}) == 2
    assert "LLM_MODEL_" in capsys.readouterr().err


def test_bedrock_without_aws_region_fails_at_startup_from_the_command_line() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "LLM_"))}
    result = subprocess.run(
        [sys.executable, "-m", "onb_llm.health", "--connector", "bedrock", "--config", str(REPO_CONFIG)],
        env={**env, **MODEL_ENV},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 2
    assert "'bedrock'" in result.stderr
    assert "AWS_REGION is not set" in result.stderr
    assert "Traceback" not in result.stderr
