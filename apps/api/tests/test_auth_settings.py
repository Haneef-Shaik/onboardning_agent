"""`JWT_SECRET` comes from the environment, and the API refuses to start without it (AUTH-1 box 4)."""

import base64
import os
import secrets
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from onb_api import create_app
from onb_api.auth.settings import MIN_SECRET_BYTES, AuthSettings, ConfigError

GOOD_SECRET = "s" * MIN_SECRET_BYTES


def test_settings_read_the_secret_from_the_environment() -> None:
    settings = AuthSettings.from_env({"JWT_SECRET": GOOD_SECRET})
    assert settings.jwt_secret == GOOD_SECRET
    assert settings.token_ttl == timedelta(minutes=60)


def test_settings_read_the_token_lifetime_from_the_environment() -> None:
    settings = AuthSettings.from_env({"JWT_SECRET": GOOD_SECRET, "JWT_TTL_MINUTES": "15"})
    assert settings.token_ttl == timedelta(minutes=15)


@pytest.mark.parametrize("ttl", ["0", "-5", "1441", "soon", ""])
def test_a_bad_token_lifetime_is_refused(ttl: str) -> None:
    with pytest.raises(ConfigError, match="JWT_TTL_MINUTES"):
        AuthSettings.from_env({"JWT_SECRET": GOOD_SECRET, "JWT_TTL_MINUTES": ttl})


@pytest.mark.parametrize("env", [{}, {"JWT_SECRET": ""}, {"JWT_SECRET": "   "}])
def test_settings_fail_without_jwt_secret(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="JWT_SECRET is not set"):
        AuthSettings.from_env(env)


def test_settings_fail_with_a_short_jwt_secret() -> None:
    with pytest.raises(ConfigError, match="JWT_SECRET must be at least"):
        AuthSettings.from_env({"JWT_SECRET": "s" * (MIN_SECRET_BYTES - 1)})


def test_the_secret_never_appears_in_the_settings_repr() -> None:
    assert GOOD_SECRET not in repr(AuthSettings.from_env({"JWT_SECRET": GOOD_SECRET}))


def test_ephemeral_settings_have_a_fresh_random_secret() -> None:
    first, second = AuthSettings.ephemeral(), AuthSettings.ephemeral()
    assert len(first.jwt_secret.encode()) >= MIN_SECRET_BYTES
    assert first.jwt_secret != second.jwt_secret


@pytest.mark.parametrize("rounds", [3, 16])
def test_bcrypt_rounds_are_bounded(rounds: int) -> None:
    with pytest.raises(ConfigError, match="bcrypt"):
        AuthSettings(jwt_secret=GOOD_SECRET, bcrypt_rounds=rounds)


def test_create_app_fails_without_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ConfigError, match="JWT_SECRET is not set"):
        create_app()


def _start_api(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import the ASGI entry point the way `uvicorn onb_api.main:app` does, in a clean process."""
    stripped = {"JWT_SECRET", "MONGO_URI", "MONGO_DATABASE", "PII_MASTER_KEY", "CONFIG_DIR"}
    base = {k: v for k, v in os.environ.items() if k not in stripped}
    base["OTEL_SDK_DISABLED"] = "true"
    return subprocess.run(
        [sys.executable, "-c", "import onb_api.main"],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


MONGO_ENV = {
    "MONGO_URI": "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=1",
    "MONGO_DATABASE": "onb",
    # HIRE-1: the vault's master key (PRD §7.10), a fresh one per run.
    "PII_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
}


def test_startup_fails_without_jwt_secret() -> None:
    result = _start_api(MONGO_ENV)
    assert result.returncode != 0
    assert "ConfigError: JWT_SECRET is not set" in result.stderr


def test_startup_fails_with_a_short_jwt_secret() -> None:
    sentinel = secrets.token_hex(8)  # 16 characters: too short, and distinctive enough to search for
    result = _start_api({**MONGO_ENV, "JWT_SECRET": sentinel})
    assert result.returncode != 0
    assert "JWT_SECRET must be at least" in result.stderr
    assert sentinel not in result.stderr


def test_startup_fails_without_the_mongo_settings() -> None:
    result = _start_api({"JWT_SECRET": GOOD_SECRET})
    assert result.returncode != 0
    assert "ConfigError: MONGO_URI is not set" in result.stderr


def test_startup_fails_without_the_pii_master_key() -> None:
    env = {k: v for k, v in MONGO_ENV.items() if k != "PII_MASTER_KEY"}
    result = _start_api({**env, "JWT_SECRET": GOOD_SECRET})
    assert result.returncode != 0
    assert "MasterKeyError: PII_MASTER_KEY is not set" in result.stderr


def test_startup_fails_with_a_malformed_pii_master_key() -> None:
    sentinel = base64.b64encode(secrets.token_bytes(16)).decode("ascii")  # 16 bytes: too short
    result = _start_api({**MONGO_ENV, "JWT_SECRET": GOOD_SECRET, "PII_MASTER_KEY": sentinel})
    assert result.returncode != 0
    assert "PII_MASTER_KEY must be base64 of exactly 32 bytes" in result.stderr
    assert sentinel not in result.stderr


CONFIG_ENV = {"CONFIG_DIR": str(Path(__file__).resolve().parents[3] / "config")}


def test_startup_succeeds_with_the_settings() -> None:
    result = _start_api({**MONGO_ENV, **CONFIG_ENV, "JWT_SECRET": GOOD_SECRET})
    assert result.returncode == 0, result.stderr


def test_startup_fails_without_the_config_dir() -> None:
    """TPL-2: the template validator reads the tool catalog and the sink policy from CONFIG_DIR."""
    result = _start_api({**MONGO_ENV, "JWT_SECRET": GOOD_SECRET})
    assert result.returncode != 0
    assert "ConfigError: CONFIG_DIR is not set" in result.stderr
