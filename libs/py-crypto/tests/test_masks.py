"""Display masks (PRD §7.10 Display, FR-P1). Every input is synthetic."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from onb_crypto import MaskedField, mask, mask_aadhaar, mask_bank_account, mask_email, mask_mobile, mask_pan

# PRD §7.10 Display: "mobile 98XXXXXX21, email j***@domain.com, Aadhaar XXXX XXXX 1234, PAN ABXXXXX4F,
# account XXXXXX6789". Each input is a synthetic value that ends (and starts) the way the example does.
PRD_MASK_EXAMPLES = [
    (MaskedField.MOBILE, "9800000021", "98XXXXXX21"),
    (MaskedField.EMAIL, "jane.doe@domain.com", "j***@domain.com"),
    (MaskedField.AADHAAR, "999900001234", "XXXX XXXX 1234"),
    (MaskedField.PAN, "ABCDE1234F", "ABXXXXX4F"),
    (MaskedField.BANK_ACCOUNT, "000123456789", "XXXXXX6789"),
]


@pytest.mark.parametrize(("field", "value", "expected"), PRD_MASK_EXAMPLES)
def test_each_prd_mask_example_produces_exactly_the_prd_string(field: MaskedField, value: str, expected: str) -> None:
    assert mask(field, value) == expected


def test_the_prd_table_covers_every_masked_field() -> None:
    assert {field for field, _, _ in PRD_MASK_EXAMPLES} == set(MaskedField)


@pytest.mark.parametrize("value", ["9800000021", "+919800000021", "+91 98000 00021", "+91-98000-00021", "098000 00021"])
def test_mobile_mask_ignores_layout(value: str) -> None:
    assert mask_mobile(value) == "98XXXXXX21"


@pytest.mark.parametrize("value", ["999900001234", "9999 0000 1234", "9999-0000-1234"])
def test_aadhaar_mask_ignores_layout(value: str) -> None:
    assert mask_aadhaar(value) == "XXXX XXXX 1234"


def test_pan_mask_reads_lowercase_input() -> None:
    assert mask_pan(" abcde1234f ") == "ABXXXXX4F"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("j@domain.com", "j***@domain.com"), ("Jane@Mail.Example.in", "J***@Mail.Example.in")],
)
def test_email_mask_keeps_first_character_and_domain(value: str, expected: str) -> None:
    assert mask_email(value) == expected


def test_bank_account_mask_has_a_fixed_width_whatever_the_length() -> None:
    assert mask_bank_account("123456789") == "XXXXXX6789"
    assert mask_bank_account("123456789012346789") == "XXXXXX6789"


@pytest.mark.parametrize(
    ("func", "bad"),
    [
        (mask_mobile, "5800000021"),
        (mask_mobile, "980000002"),
        (mask_email, "jane.doe"),
        (mask_email, "@domain.com"),
        (mask_aadhaar, "99990000123"),
        (mask_aadhaar, "9999 0000-1234"),
        (mask_pan, "ABCD1234F"),
        (mask_bank_account, "12345678"),
        (mask_bank_account, "12345678a9"),
    ],
)
def test_mask_rejects_malformed_input_without_echoing_it(func: object, bad: str) -> None:
    assert callable(func)
    with pytest.raises(ValueError, match="malformed") as excinfo:
        func(bad)
    assert bad not in str(excinfo.value)


@given(st.from_regex(r"[6-9][0-9]{9}", fullmatch=True))
def test_mobile_mask_reveals_only_first_two_and_last_two_digits(value: str) -> None:
    masked = mask_mobile(value)
    assert masked == value[:2] + "X" * 6 + value[-2:]


@given(st.from_regex(r"[0-9]{9,18}", fullmatch=True))
def test_bank_account_mask_reveals_only_last_four_digits(value: str) -> None:
    assert mask_bank_account(value) == "XXXXXX" + value[-4:]
