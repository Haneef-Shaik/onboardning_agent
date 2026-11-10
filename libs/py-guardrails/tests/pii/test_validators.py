"""Field validators (PRD §7.10 Aadhaar validation; card PII-3 scope).

Every value here is synthetic: Aadhaar numbers are random digits with a computed Verhoeff check
digit, mobiles use the 90000 test range, emails use example.com.
"""

import pytest
from onb_guardrails.pii import (
    is_valid_aadhaar,
    is_valid_bank_account,
    is_valid_email,
    is_valid_ifsc,
    is_valid_mobile,
    is_valid_pan,
    verhoeff_check_digit,
    verhoeff_is_valid,
)

# Verhoeff vectors. "2363" is the textbook worked example (the check digit of "236" is 3).
# "999900001231" is the synthetic Aadhaar in evals/fixtures/seed_v1.json. The rest are random synthetic
# digits. Every vector was cross-checked against python-stdnum (stdnum.verhoeff, stdnum.in_.aadhaar), an
# independent implementation; see docs/evidence/PII-3/verhoeff-oracle.txt.
VERHOEFF_VALID = ["2363", "0", "999900001231", "334249855769", "529425329254", "917072196529", "200684941428"]
VERHOEFF_INVALID = ["2364", "2336", "1", "334249855760", "848589512865", "509921984961", "334249855766"]


@pytest.mark.parametrize("number", VERHOEFF_VALID)
def test_verhoeff_accepts_valid_vectors(number: str) -> None:
    assert verhoeff_is_valid(number)


@pytest.mark.parametrize("number", VERHOEFF_INVALID)
def test_verhoeff_rejects_invalid_vectors(number: str) -> None:
    assert not verhoeff_is_valid(number)


@pytest.mark.parametrize(("payload", "check"), [("236", "3"), ("33424985576", "9"), ("99990000123", "1")])
def test_verhoeff_check_digit_matches_vectors(payload: str, check: str) -> None:
    assert verhoeff_check_digit(payload) == check


def test_verhoeff_catches_every_single_digit_error() -> None:
    number = "334249855769"
    for i, original in enumerate(number):
        for digit in "0123456789":
            if digit != original:
                assert not verhoeff_is_valid(number[:i] + digit + number[i + 1 :])


def test_verhoeff_catches_every_adjacent_transposition() -> None:
    number = "529425329254"
    for i in range(len(number) - 1):
        if number[i] != number[i + 1]:
            swapped = number[:i] + number[i + 1] + number[i] + number[i + 2 :]
            assert not verhoeff_is_valid(swapped)


@pytest.mark.parametrize("bad", ["", "12a4", "12 34", "١٢٣"])
def test_verhoeff_rejects_non_ascii_digit_input(bad: str) -> None:
    assert not verhoeff_is_valid(bad)
    with pytest.raises(ValueError, match="ASCII digits"):
        verhoeff_check_digit(bad)


@pytest.mark.parametrize("aadhaar", ["334249855769", "3342 4985 5769", "3342-4985-5769", "9999 0000 1231"])
def test_aadhaar_accepts_valid_numbers_in_common_layouts(aadhaar: str) -> None:
    assert is_valid_aadhaar(aadhaar)


# Both leading-digit cases carry a correct Verhoeff check digit, so only the 0/1 rule rejects them.
@pytest.mark.parametrize("aadhaar", ["023456789014", "123456789010", "0234 5678 9014", "1234 5678 9010"])
def test_aadhaar_starting_with_0_or_1_is_rejected(aadhaar: str) -> None:
    assert verhoeff_is_valid(aadhaar.replace(" ", ""))
    assert not is_valid_aadhaar(aadhaar)


FULL_WIDTH_AADHAAR = "334249855769".translate({ord(d): ord(d) + 0xFEE0 for d in "0123456789"})


@pytest.mark.parametrize(
    "aadhaar",
    [
        "334249855760",  # Verhoeff fails: a 12-digit order id is not Aadhaar
        "848589512865",
        "33424985576",  # 11 digits
        "3342498557660",  # 13 digits
        "3342 49855769",  # mixed grouping
        "3342  4985 5769",  # double space
        "3342 4985-5769",  # mixed separators
        "",
        "33424985576x",
        FULL_WIDTH_AADHAAR,  # full-width digits look the same but are not ASCII
    ],
)
def test_aadhaar_rejects_bad_numbers(aadhaar: str) -> None:
    assert not is_valid_aadhaar(aadhaar)


@pytest.mark.parametrize("pan", ["ABCDE1234F", "AAAPZ9999Z"])
def test_pan_accepts_prd_shape(pan: str) -> None:
    assert is_valid_pan(pan)


@pytest.mark.parametrize(
    "pan", ["abcde1234f", "ABCD1234F", "ABCDE12345", "ABCDE1234FG", "1BCDE1234F", " ABCDE1234F", ""]
)
def test_pan_rejects_other_shapes(pan: str) -> None:
    assert not is_valid_pan(pan)


@pytest.mark.parametrize("ifsc", ["TEST0000001", "HDFC0001234", "SBIN0ABC123"])
def test_ifsc_accepts_prd_shape(ifsc: str) -> None:
    assert is_valid_ifsc(ifsc)


@pytest.mark.parametrize("ifsc", ["TEST1000001", "TES00000001", "TEST000001", "TEST00000012", "test0000001", ""])
def test_ifsc_rejects_other_shapes(ifsc: str) -> None:
    assert not is_valid_ifsc(ifsc)


@pytest.mark.parametrize("mobile", ["9000000001", "+919000000001", "+91 90000 00001", "+91-90000-00001", "6000000000"])
def test_mobile_accepts_indian_numbers(mobile: str) -> None:
    assert is_valid_mobile(mobile)


@pytest.mark.parametrize("mobile", ["5000000001", "900000000", "90000000011", "+929000000001", "+91 9000 000 001", ""])
def test_mobile_rejects_other_numbers(mobile: str) -> None:
    assert not is_valid_mobile(mobile)


@pytest.mark.parametrize("email", ["priya.sharma@example.com", "a+tag@mail.example.co.in", "x_y-z@example.org"])
def test_email_accepts_addresses(email: str) -> None:
    assert is_valid_email(email)


@pytest.mark.parametrize(
    "email", ["priya", "@example.com", "priya@", "priya@localhost", "a@@example.com", "a b@x.com", ""]
)
def test_email_rejects_non_addresses(email: str) -> None:
    assert not is_valid_email(email)


@pytest.mark.parametrize("account", ["000111222333", "123456789", "123456789012345678"])
def test_bank_account_accepts_9_to_18_digits(account: str) -> None:
    assert is_valid_bank_account(account)


@pytest.mark.parametrize("account", ["12345678", "1234567890123456789", "12345 6789", "12345678a", ""])
def test_bank_account_rejects_other_values(account: str) -> None:
    assert not is_valid_bank_account(account)
