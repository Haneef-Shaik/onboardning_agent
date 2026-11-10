"""PII tokens: `ptk_` + ULID + 128 random bits, all randomness from `secrets` (PRD §7.10 Tokens)."""

import ast
import inspect
import re
from datetime import UTC, datetime, timedelta

import pytest
from onb_crypto import tokens
from onb_crypto.tokens import PTK_PREFIX, TOKEN_LENGTH, is_token, new_token

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SHAPE = re.compile(rf"ptk_[{_CROCKFORD}]{{52}}")
_EPOCH_2026 = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)


def _decode(text: str) -> int:
    value = 0
    for char in text:
        value = value * 32 + _CROCKFORD.index(char)
    return value


def test_tokens_have_the_documented_prefix_and_length() -> None:
    token = new_token()
    assert PTK_PREFIX == "ptk_"
    assert TOKEN_LENGTH == 56 == len("ptk_") + 26 + 26  # prefix + ULID + 128 bits in Crockford base32
    assert token.startswith("ptk_")
    assert len(token) == TOKEN_LENGTH
    assert _SHAPE.fullmatch(token), token
    assert is_token(token)


def test_the_ulid_part_carries_the_creation_time_and_nothing_about_a_value() -> None:
    token = new_token(now=_EPOCH_2026)
    millis = _decode(token[4:14])
    assert millis == int(_EPOCH_2026.timestamp() * 1000)
    later = new_token(now=_EPOCH_2026 + timedelta(milliseconds=1))
    assert later[4:14] > token[4:14], "ULIDs sort by time"


def test_tokens_use_secrets_for_every_random_bit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_token_bytes(n: int) -> bytes:
        calls.append(n)
        return bytes([0xFF]) * n

    monkeypatch.setattr("onb_crypto.tokens.secrets.token_bytes", fake_token_bytes)
    token = new_token(now=_EPOCH_2026)
    assert calls == [10, 16], "80 ULID bits and 128 extra bits, both from secrets.token_bytes"
    assert _decode(token[14:30]) == 2**80 - 1
    assert _decode(token[30:]) == 2**128 - 1


def test_the_token_module_imports_no_other_randomness_source() -> None:
    tree = ast.parse(inspect.getsource(tokens))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "secrets" in imported
    assert not imported & {"random", "uuid", "numpy", "os"}


def test_tokens_are_unique() -> None:
    sample = {new_token(now=_EPOCH_2026) for _ in range(2000)}
    assert len(sample) == 2000


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "ptk_",
        "ptk_" + "0" * 51,
        "ptk_" + "0" * 53,
        "PTK_" + "0" * 52,
        "tok_" + "0" * 52,
        "ptk_" + "I" * 52,  # not Crockford
        "ptk_" + "u" * 52,  # lower case is not issued
        "ptk_8" + "0" * 51,  # timestamp beyond 48 bits
        "ptk_" + "0" * 26 + "8" + "0" * 25,  # random part beyond 128 bits
        None,
        12,
    ],
)
def test_is_token_refuses_anything_not_issued_here(candidate: object) -> None:
    assert not is_token(candidate)


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        new_token(now=datetime(2026, 11, 10, 6, 0))  # the naive datetime is the point
