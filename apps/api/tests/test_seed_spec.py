"""The seed file and its passwords (SEED-1 box 3; PRD §7.11): passwords come from the environment only.

`config/seed.yaml` names an environment variable per user, never a password. Loading it reads those
variables; a missing, empty or short one stops the seed with a message that names the variable and
never its value. A password written into the file itself is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from onb_api.seed.spec import MIN_PASSWORD_LENGTH, SeedConfigError, load_seed
from onb_contracts.auth import Role

ROOT = Path(__file__).resolve().parents[3]
SEED_FILE = ROOT / "config" / "seed.yaml"
GOOD_ENV = {
    "SEED_HR_ADMIN_PASSWORD": "admin-pass-0123456789",
    "SEED_HR_PASSWORD": "hr-pass-0123456789",
    "SEED_MANAGER_PASSWORD": "manager-pass-0123456789",
}


def _raw() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SEED_FILE.read_text())
    return loaded


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    raw = {**raw, "templates": [str(ROOT / "templates" / "engineer-india.yaml")]}
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_the_shipped_seed_file_loads_with_passwords_from_the_environment() -> None:
    plan = load_seed(SEED_FILE, GOOD_ENV)
    assert plan.org.id == "org_demo"
    roles = [u.role for u in plan.users]
    assert roles.count(Role.HR_ADMIN) == 1
    assert roles.count(Role.HR) == 1
    assert roles.count(Role.MANAGER) >= 2
    assert plan.actor.role is Role.HR_ADMIN
    passwords = {u.id: u.password for u in plan.users}
    assert passwords["u_demo_hradmin"] == GOOD_ENV["SEED_HR_ADMIN_PASSWORD"]
    assert passwords["u_demo_mgr_priya"] == GOOD_ENV["SEED_MANAGER_PASSWORD"]
    assert [t.key for t in plan.templates] == ["engineer-india"]


def test_the_shipped_seed_file_holds_no_password() -> None:
    text = SEED_FILE.read_text()
    for value in GOOD_ENV.values():
        assert value not in text
    for user in _raw()["users"]:
        assert set(user) == {"id", "email", "name", "role", "password_env"}


def test_a_password_never_shows_in_a_repr() -> None:
    plan = load_seed(SEED_FILE, GOOD_ENV)
    assert GOOD_ENV["SEED_HR_ADMIN_PASSWORD"] not in repr(plan)
    assert GOOD_ENV["SEED_HR_ADMIN_PASSWORD"] not in repr(plan.users[0])


@pytest.mark.parametrize("missing", sorted(GOOD_ENV))
def test_a_missing_password_variable_stops_the_seed_and_names_it(missing: str) -> None:
    env = {k: v for k, v in GOOD_ENV.items() if k != missing}
    with pytest.raises(SeedConfigError, match=missing):
        load_seed(SEED_FILE, env)


def test_an_empty_password_variable_is_missing() -> None:
    with pytest.raises(SeedConfigError, match="SEED_HR_PASSWORD is not set"):
        load_seed(SEED_FILE, {**GOOD_ENV, "SEED_HR_PASSWORD": "   "})


def test_a_short_password_is_refused_without_echoing_it() -> None:
    short = "x" * (MIN_PASSWORD_LENGTH - 1)
    with pytest.raises(SeedConfigError, match="SEED_HR_PASSWORD") as caught:
        load_seed(SEED_FILE, {**GOOD_ENV, "SEED_HR_PASSWORD": short})
    assert short not in str(caught.value)


def test_a_password_longer_than_bcrypt_reads_is_refused_without_echoing_it() -> None:
    long = "é" * 37  # 74 bytes
    with pytest.raises(SeedConfigError, match="SEED_HR_PASSWORD") as caught:
        load_seed(SEED_FILE, {**GOOD_ENV, "SEED_HR_PASSWORD": long})
    assert long not in str(caught.value)


def test_a_password_written_into_the_seed_file_is_refused(tmp_path: Path) -> None:
    raw = _raw()
    raw["users"][0] = {**raw["users"][0], "password": "inline-pass-0123456789"}
    with pytest.raises(SeedConfigError, match="password"):
        load_seed(_write(tmp_path, raw), GOOD_ENV)


def test_a_password_variable_must_be_a_seed_password_name(tmp_path: Path) -> None:
    raw = _raw()
    raw["users"][0] = {**raw["users"][0], "password_env": "MONGO_ROOT_PASSWORD"}
    with pytest.raises(SeedConfigError, match="password_env"):
        load_seed(_write(tmp_path, raw), {**GOOD_ENV, "MONGO_ROOT_PASSWORD": "root-pass-0123456789"})


@pytest.mark.parametrize("role", ["HR_ADMIN", "HR", "MANAGER"])
def test_the_seed_needs_every_role(tmp_path: Path, role: str) -> None:
    raw = _raw()
    raw["users"] = [u for u in raw["users"] if u["role"] != role]
    with pytest.raises(SeedConfigError, match=role):
        load_seed(_write(tmp_path, raw), GOOD_ENV)


def test_two_users_cannot_share_an_email(tmp_path: Path) -> None:
    raw = _raw()
    raw["users"][3] = {**raw["users"][3], "email": " Priya.Manager@Demo.Example "}
    with pytest.raises(SeedConfigError, match="email"):
        load_seed(_write(tmp_path, raw), GOOD_ENV)


def test_two_users_cannot_share_an_id(tmp_path: Path) -> None:
    raw = _raw()
    raw["users"][3] = {**raw["users"][3], "id": raw["users"][2]["id"]}
    with pytest.raises(SeedConfigError, match="id"):
        load_seed(_write(tmp_path, raw), GOOD_ENV)


def test_two_templates_cannot_share_a_key(tmp_path: Path) -> None:
    path = _write(tmp_path, _raw())
    raw = yaml.safe_load(path.read_text())
    raw["templates"] = raw["templates"] * 2
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(SeedConfigError, match="engineer-india"):
        load_seed(path, GOOD_ENV)


def test_a_template_that_is_not_a_template_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("key: broken\nname: Broken\ntasks: nope\n")
    path = _write(tmp_path, _raw())
    raw = yaml.safe_load(path.read_text())
    raw["templates"] = [str(broken)]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(SeedConfigError, match=r"broken\.yaml"):
        load_seed(path, GOOD_ENV)


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "org-Demo!"), ("id", "acme")],
)
def test_an_org_id_must_be_an_org_id(tmp_path: Path, field: str, value: str) -> None:
    raw = _raw()
    raw["org"] = {**raw["org"], field: value}
    with pytest.raises(SeedConfigError, match="org"):
        load_seed(_write(tmp_path, raw), GOOD_ENV)


def test_a_missing_seed_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(SeedConfigError, match="not found"):
        load_seed(tmp_path / "nope.yaml", GOOD_ENV)


def test_a_seed_file_that_is_not_yaml_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "seed.yaml"
    path.write_text("org: [unclosed\n")
    with pytest.raises(SeedConfigError, match=r"seed\.yaml"):
        load_seed(path, GOOD_ENV)


def test_a_password_with_surrounding_whitespace_is_refused_without_echoing_it() -> None:
    padded = " hr-pass-0123456789 "
    with pytest.raises(SeedConfigError, match="SEED_HR_PASSWORD must not start or end with whitespace") as caught:
        load_seed(SEED_FILE, {**GOOD_ENV, "SEED_HR_PASSWORD": padded})
    assert padded.strip() not in str(caught.value)
