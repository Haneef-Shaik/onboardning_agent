"""`python -m onb_api.retention`: configuration is checked first and a refusal prints no secret (PII-5)."""

from __future__ import annotations

import pytest
from onb_api.auth.settings import ConfigError
from onb_api.retention.__main__ import DEFAULT_INTERVAL_SECONDS, interval_from, main

URI = "mongodb://user:s3cretpw@db.invalid:27017"


def test_the_interval_defaults_to_an_hour() -> None:
    assert interval_from({}) == DEFAULT_INTERVAL_SECONDS == 3600.0


def test_the_interval_comes_from_the_environment() -> None:
    assert interval_from({"PII_PURGE_INTERVAL_SECONDS": "60"}) == 60.0


@pytest.mark.parametrize("bad", ["soon", "nan", "inf", "0", "-5", "0.5"])
def test_a_bad_interval_is_refused(bad: str) -> None:
    with pytest.raises(ConfigError):
        interval_from({"PII_PURGE_INTERVAL_SECONDS": bad})


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"MONGO_URI": URI},
        {"MONGO_URI": URI, "MONGO_DATABASE": "onb"},
        {"MONGO_URI": URI, "MONGO_DATABASE": "onb", "PII_MASTER_KEY": "not-base64-of-32-bytes"},
    ],
)
def test_missing_or_bad_settings_stop_the_job_before_it_touches_the_database(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--once"], env) == 1
    err = capsys.readouterr().err
    assert err.startswith("pii retention purge stopped: ")
    assert "s3cretpw" not in err
    assert "not-base64" not in err
