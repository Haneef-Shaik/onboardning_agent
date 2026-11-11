"""`python -m onb_api.seed` (SEED-1; PRD §7.11): configuration from the environment, refusals in one
line that never repeat a password, a key or the Mongo URI. The command against MongoDB is in
`tests_int/test_mongo_seed.py`."""

from __future__ import annotations

import argparse
import base64
import secrets
from collections.abc import Mapping
from pathlib import Path

import pytest
from onb_agents.templates import Issue, Rule
from onb_api.seed import __main__ as command
from onb_api.seed.seeder import SeedConflictError, SeedReport, SeedStep
from onb_api.templates import LifecycleConflictError, TemplateRejectedError
from pymongo.errors import ServerSelectionTimeoutError

ROOT = Path(__file__).resolve().parents[3]
PASSWORDS = {
    "SEED_HR_ADMIN_PASSWORD": "admin-pass-0123456789",
    "SEED_HR_PASSWORD": "hr-pass-0123456789",
    "SEED_MANAGER_PASSWORD": "manager-pass-0123456789",
}
MASTER = base64.b64encode(secrets.token_bytes(32)).decode()
URI = "mongodb://seed-user:very-secret-db-pass@db.internal:27017/?authSource=admin"
ENV = {**PASSWORDS, "PII_MASTER_KEY": MASTER, "MONGO_URI": URI, "MONGO_DATABASE": "onboarding"}
ARGS = ["--seed", str(ROOT / "config" / "seed.yaml"), "--config", str(ROOT / "config")]


def _secrets_absent(text: str) -> None:
    for value in (*PASSWORDS.values(), MASTER, "very-secret-db-pass", "db.internal"):
        assert value not in text


def test_the_defaults_are_the_repository_config() -> None:
    args = command.parse_args([])
    assert (args.seed, args.config) == (Path("config/seed.yaml"), Path("config"))


@pytest.mark.parametrize("missing", ["SEED_HR_PASSWORD", "PII_MASTER_KEY", "MONGO_URI", "MONGO_DATABASE"])
def test_a_missing_setting_is_refused_before_any_connection(
    missing: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_connection(*_: object, **__: object) -> None:
        raise AssertionError("connected before the configuration was checked")

    monkeypatch.setattr(command, "connect", no_connection)
    env = {k: v for k, v in ENV.items() if k != missing}
    assert command.main(ARGS, env) == 1
    err = capsys.readouterr().err
    assert err.startswith("seed refused: ")
    assert missing in err
    _secrets_absent(err)


def test_a_master_key_that_is_not_a_key_is_refused_without_echoing_it(capsys: pytest.CaptureFixture[str]) -> None:
    assert command.main(ARGS, {**ENV, "PII_MASTER_KEY": "not-base64-of-32-bytes!"}) == 1
    err = capsys.readouterr().err
    assert "PII_MASTER_KEY" in err
    assert "not-base64-of-32-bytes!" not in err


def _raising(error: BaseException) -> object:
    async def seed(_: argparse.Namespace, __: Mapping[str, str]) -> SeedReport:
        raise error

    return seed


def test_a_conflict_is_refused_in_one_line(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command, "_seed", _raising(SeedConflictError("u_demo_hr is registered to another user")))
    assert command.main(ARGS, ENV) == 1
    assert capsys.readouterr().err == "seed refused: u_demo_hr is registered to another user\n"


def test_an_invalid_seed_template_names_its_rules_and_tasks(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = (Issue(Rule.UNKNOWN_AGENT, "laptop", "no agent x"), Issue(Rule.CYCLE, None, "a cycle"))
    monkeypatch.setattr(command, "_seed", _raising(TemplateRejectedError(issues)))
    assert command.main(ARGS, ENV) == 1
    err = capsys.readouterr().err
    assert (
        err == f"seed refused: a seed template is invalid ({Rule.CYCLE.value}@-, {Rule.UNKNOWN_AGENT.value}@laptop)\n"
    )


def test_a_database_error_names_its_type_only(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(command, "_seed", _raising(ServerSelectionTimeoutError(f"{URI}: timed out")))
    assert command.main(ARGS, ENV) == 1
    err = capsys.readouterr().err
    assert err == "seed failed: MongoDB error ServerSelectionTimeoutError\n"
    _secrets_absent(err)


def test_a_finished_seed_prints_one_line_per_record_and_a_summary(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    report = SeedReport(steps=(SeedStep("created", "org:org_demo"), SeedStep("unchanged", "agent:db_agent")))

    async def seed(_: argparse.Namespace, __: Mapping[str, str]) -> SeedReport:
        return report

    monkeypatch.setattr(command, "_seed", seed)
    assert command.main(ARGS, ENV) == 0
    assert capsys.readouterr().out == "created org:org_demo\nunchanged agent:db_agent\n1 created, 1 unchanged\n"


def test_a_concurrent_publish_is_refused_in_one_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conflict = LifecycleConflictError("another version of this template was published at the same time")
    monkeypatch.setattr(command, "_seed", _raising(conflict))
    assert command.main(ARGS, ENV) == 1
    assert capsys.readouterr().err == f"seed refused: {conflict}\n"
