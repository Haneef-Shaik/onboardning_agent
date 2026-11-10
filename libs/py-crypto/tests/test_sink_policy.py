"""The field → sink policy (PRD §7.10 Signed grants): which vault fields an agent sink may ever resolve.

The policy lives in `config/pii_sink_policy.yaml`. Aadhaar, PAN and the bank fields can never be given
to a sink: the loader refuses a file that tries, so a config edit cannot open them up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from onb_crypto import NEVER_SINKABLE, SinkPolicy, SinkPolicyError, VaultField, load_sink_policy

REPO = Path(__file__).resolve().parents[3]
POLICY_FILE = REPO / "config" / "pii_sink_policy.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    return path


def test_the_shipped_policy_matches_the_prd() -> None:
    policy = load_sink_policy(POLICY_FILE)
    assert policy.fields_for("notify.telegram") == frozenset({VaultField.MOBILE})
    assert policy.fields_for("notify.email") == frozenset({VaultField.EMAIL})
    assert policy.sinks == frozenset({"notify.telegram", "notify.email"})


@pytest.mark.parametrize("field", sorted(NEVER_SINKABLE))
def test_no_sink_may_ever_resolve_aadhaar_pan_or_bank_fields(field: VaultField) -> None:
    policy = load_sink_policy(POLICY_FILE)
    assert not any(policy.allows(field, sink) for sink in policy.sinks)


def test_never_sinkable_covers_aadhaar_pan_and_every_bank_field() -> None:
    assert (
        frozenset(
            {
                VaultField.AADHAAR,
                VaultField.PAN,
                VaultField.BANK_ACCOUNT,
                VaultField.IFSC,
                VaultField.ACCOUNT_HOLDER_NAME,
            }
        )
        == NEVER_SINKABLE
    )


def test_allows_is_per_field_and_sink() -> None:
    policy = load_sink_policy(POLICY_FILE)
    assert policy.allows(VaultField.MOBILE, "notify.telegram")
    assert not policy.allows(VaultField.MOBILE, "notify.email")
    assert not policy.allows(VaultField.EMAIL, "notify.telegram")
    assert not policy.allows(VaultField.MOBILE, "notify.sms")
    assert policy.fields_for("notify.sms") == frozenset()


@pytest.mark.parametrize("field", ["aadhaar", "pan", "bank_account", "ifsc", "account_holder_name"])
def test_a_config_that_grants_a_never_sinkable_field_is_refused(tmp_path: Path, field: str) -> None:
    path = write(tmp_path, f"version: 1\nsinks:\n  notify.telegram: [mobile, {field}]\n")
    with pytest.raises(SinkPolicyError, match="never"):
        load_sink_policy(path)


def test_the_policy_object_itself_refuses_a_never_sinkable_field() -> None:
    with pytest.raises(SinkPolicyError, match="never"):
        SinkPolicy({"notify.telegram": frozenset({VaultField.AADHAAR})})


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "mapping"),
        ("- a\n", "mapping"),
        ("version: 2\nsinks: {}\n", "version"),
        ("sinks:\n  notify.telegram: [mobile]\n", "version"),
        ("version: 1\n", "sinks"),
        ("version: 1\nsinks: []\n", "sinks"),
        ("version: 1\nsinks:\n  notify.telegram: mobile\n", "list"),
        ("version: 1\nsinks:\n  notify.telegram: []\n", "at least one"),
        ("version: 1\nsinks:\n  notify.telegram: [phone]\n", "unknown field"),
        ("version: 1\nsinks:\n  notify.telegram: [mobile, mobile]\n", "twice"),
        ("version: 1\nsinks:\n  Notify Telegram: [mobile]\n", "sink id"),
        ("version: 1\nsinks:\n  telegram: [mobile]\n", "sink id"),
        ("version: 1\nsinks:\n  notify.telegram: [mobile]\nextra: true\n", "unexpected"),
        ("version: 1\nsinks:\n  notify.telegram: [1]\n", "unknown field"),
        ("version: 1\nsinks: {notify.telegram: [mobile]\n", "YAML"),
    ],
)
def test_a_malformed_policy_file_is_refused(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(SinkPolicyError, match=match):
        load_sink_policy(write(tmp_path, text))


def test_a_missing_policy_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SinkPolicyError, match="cannot read"):
        load_sink_policy(tmp_path / "absent.yaml")


def test_the_policy_is_immutable() -> None:
    policy = load_sink_policy(POLICY_FILE)
    with pytest.raises(AttributeError):
        policy.sinks = frozenset()  # type: ignore[misc]  # proving immutability
