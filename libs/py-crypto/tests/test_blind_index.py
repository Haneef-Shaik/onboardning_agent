"""HMAC-SHA256 blind indexes for lookup and dedupe (PRD §7.10 Lookup / dedupe)."""

import pytest
from onb_crypto import MIN_INDEX_KEY_BYTES, BlindIndexField, blind_index

# A fixed test key (bytes 0x00..0x1f), not a secret. Expected digests were computed independently with
#   printf 'mobile\x1f9000000001' | openssl dgst -sha256 -mac HMAC -macopt hexkey:000102...1e1f
TEST_KEY = bytes(range(32))
OTHER_ORG_KEY = bytes(range(1, 33))


@pytest.mark.parametrize(
    ("field", "value", "digest"),
    [
        (BlindIndexField.MOBILE, "9000000001", "f8182dabce93cad84f30913d97c7e52f35bb393bb38c74276bd9729383096b02"),
        (
            BlindIndexField.EMAIL,
            "priya.sharma@example.com",
            "e3e654f89f0bddc4949fd94425179042f26262f4c5830ec054017b40a7ceaf0b",
        ),
    ],
)
def test_blind_index_matches_independent_hmac_sha256(field: BlindIndexField, value: str, digest: str) -> None:
    assert blind_index(TEST_KEY, field, value) == digest


@pytest.mark.parametrize(
    ("field", "a", "b"),
    [
        (BlindIndexField.MOBILE, "9000000001", "+91 90000 00001"),
        (BlindIndexField.MOBILE, "9000000001", "09000000001"),
        (BlindIndexField.EMAIL, "priya.sharma@example.com", "  Priya.Sharma@EXAMPLE.com "),
        (BlindIndexField.PAN, "ABCDE1234F", "abcde1234f"),
        (BlindIndexField.AADHAAR, "999900001231", "9999 0000 1231"),
    ],
)
def test_layouts_of_one_value_share_an_index_so_dedupe_works(field: BlindIndexField, a: str, b: str) -> None:
    assert blind_index(TEST_KEY, field, a) == blind_index(TEST_KEY, field, b)


def test_different_values_have_different_indexes() -> None:
    assert blind_index(TEST_KEY, BlindIndexField.MOBILE, "9000000001") != blind_index(
        TEST_KEY, BlindIndexField.MOBILE, "9000000002"
    )


def test_the_same_value_indexes_differently_per_org_key() -> None:
    value = "priya.sharma@example.com"
    assert blind_index(TEST_KEY, BlindIndexField.EMAIL, value) != blind_index(
        OTHER_ORG_KEY, BlindIndexField.EMAIL, value
    )


def test_fields_are_domain_separated() -> None:
    # The same digits as a mobile and as a (malformed-for-Aadhaar) value must not collide across fields.
    assert blind_index(TEST_KEY, BlindIndexField.MOBILE, "9000000001") != blind_index(
        TEST_KEY, BlindIndexField.AADHAAR, "900000000100"
    )


def test_index_is_64_hex_chars_and_does_not_contain_the_value() -> None:
    digest = blind_index(TEST_KEY, BlindIndexField.AADHAAR, "999900001231")
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
    assert "999900001231" not in digest


def test_index_names_follow_the_prd() -> None:
    assert {field.index_name for field in BlindIndexField} == {"email_hash", "mobile_hash", "pan_hash", "aadhaar_hash"}


@pytest.mark.parametrize("key", [b"", b"short", bytes(MIN_INDEX_KEY_BYTES - 1)])
def test_short_keys_are_rejected(key: bytes) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        blind_index(key, BlindIndexField.EMAIL, "priya.sharma@example.com")


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        (BlindIndexField.MOBILE, "5000000001"),
        (BlindIndexField.EMAIL, "not-an-email"),
        (BlindIndexField.PAN, "ABCD1234F"),
        (BlindIndexField.AADHAAR, "99990000123"),
    ],
)
def test_malformed_values_are_rejected_without_echoing_them(field: BlindIndexField, bad: str) -> None:
    with pytest.raises(ValueError, match="malformed") as excinfo:
        blind_index(TEST_KEY, field, bad)
    assert bad not in str(excinfo.value)
