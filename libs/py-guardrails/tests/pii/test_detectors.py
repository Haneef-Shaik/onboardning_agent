"""Free-text PII detectors and redact() (PRD §7.10 Free-text input). All values are synthetic."""

import time

import pytest
from onb_guardrails.pii import PiiSpan, PiiType, detect, redact


def _found(text: str) -> list[tuple[PiiType, str]]:
    return [(span.type, text[span.start : span.end]) for span in detect(text)]


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Aadhaar 3342 4985 5769 ok", PiiType.AADHAAR, "3342 4985 5769"),
        ("uid:529425329254.", PiiType.AADHAAR, "529425329254"),
        ("UID 2006-8494-1428", PiiType.AADHAAR, "2006-8494-1428"),
        ("PAN ABCDE1234F.", PiiType.PAN, "ABCDE1234F"),
        ("pan abcde1234f", PiiType.PAN, "abcde1234f"),
        ("IFSC TEST0000001,", PiiType.IFSC, "TEST0000001"),
        ("ifsc hdfc0001234", PiiType.IFSC, "hdfc0001234"),
        ("call 9000000002 now", PiiType.MOBILE, "9000000002"),
        ("+91 90000 00001", PiiType.MOBILE, "+91 90000 00001"),
        ("tel +919000000003", PiiType.MOBILE, "+919000000003"),
        ("tel 09000000005", PiiType.MOBILE, "09000000005"),
        ("mail priya.sharma@example.com!", PiiType.EMAIL, "priya.sharma@example.com"),
        ("account number 000111222333", PiiType.BANK_ACCOUNT, "000111222333"),
        ("A/c no: 123456789012345", PiiType.BANK_ACCOUNT, "123456789012345"),
    ],
)
def test_each_detector_returns_a_typed_span_over_the_value(text: str, kind: PiiType, value: str) -> None:
    assert _found(text) == [(kind, value)]


def test_spans_are_frozen_ordered_and_carry_no_value() -> None:
    spans = detect("x priya.sharma@example.com y ABCDE1234F")
    assert [span.type for span in spans] == [PiiType.EMAIL, PiiType.PAN]
    assert spans[0].start < spans[1].start
    assert set(PiiSpan.__dataclass_fields__) == {"type", "start", "end"}
    with pytest.raises(AttributeError):
        spans[0].start = 0  # type: ignore[misc]  # proving the dataclass is frozen


def test_a_12_digit_number_failing_verhoeff_is_not_aadhaar() -> None:
    """Card trap: order ids are 12 digits but fail the checksum."""
    assert detect("Order 848589512865 shipped") == ()
    assert detect("Order 3342 4985 5760 shipped") == ()


def test_aadhaar_starting_with_0_or_1_is_not_detected() -> None:
    assert detect("id 1234 5678 9010 and 023456789014") == ()


def test_mobile_inside_a_12_digit_aadhaar_is_reported_as_aadhaar_only() -> None:
    """Card trap: 917219206868 is Aadhaar-valid and also reads as 91 + a mobile number."""
    assert _found("Aadhaar 917219206868") == [(PiiType.AADHAAR, "917219206868")]
    assert _found("Aadhaar 9172 1920 6868") == [(PiiType.AADHAAR, "9172 1920 6868")]
    assert redact("Aadhaar 917219206868") == "Aadhaar [AADHAAR]"


def test_mobile_is_not_found_inside_a_longer_digit_run() -> None:
    assert detect("AWB 77190000000021") == ()


def test_aadhaar_after_a_failing_group_is_still_found() -> None:
    assert _found("1111 3342 4985 5769") == [(PiiType.AADHAAR, "3342 4985 5769")]


def test_upi_handle_reveals_the_mobile() -> None:
    assert _found("pay 9000000011@ybl") == [(PiiType.MOBILE, "9000000011")]


def test_unicode_digits_cannot_hide_a_value() -> None:
    full_width = "9000000002".translate({ord(d): ord(d) + 0xFEE0 for d in "0123456789"})
    devanagari = "9000000002".translate({ord(d): ord(d) - ord("0") + 0x0966 for d in "0123456789"})
    for text in (f"call {full_width}", f"call {devanagari}"):
        assert [span.type for span in detect(text)] == [PiiType.MOBILE]
        assert redact(text) == "call [MOBILE]"


@pytest.mark.parametrize(
    "token",
    ["ptk_01JD8X3Q7M9V2K4N6P8R0T2W4YABCDE1234F", "ptk_seed_acme001_mobile", "ptk_01JD8X3Q7M9V2K4N6P8R0T2W4Y"],
)
def test_pii_tokens_are_not_detected(token: str) -> None:
    assert detect(f"recipient = {token}") == ()


def test_redact_replaces_every_value_with_its_typed_marker() -> None:
    text = (
        "Hi, I'm Priya (priya.sharma@example.com, +91 90000 00001), PAN ABCDE1234F, "
        "Aadhaar 9999 0000 1231, IFSC TEST0000001, account no 000111222333."
    )
    assert redact(text) == (
        "Hi, I'm Priya ([EMAIL], [MOBILE]), PAN [PAN], Aadhaar [AADHAAR], IFSC [IFSC], account no [BANK_ACCOUNT]."
    )


def test_redact_repeats_until_a_value_exposed_by_redaction_is_gone() -> None:
    """Found by a single-pass probe: the PAN glued to the account digits only becomes a hit once they are gone."""
    text = "account number 000000000AAAAA0000A"
    assert [span.type for span in detect(text)] == [PiiType.BANK_ACCOUNT]
    assert redact(text) == "account number [BANK_ACCOUNT][PAN]"


def test_redact_leaves_clean_text_unchanged() -> None:
    text = "Order 848589512865, pincode 560001, ₹1,23,456 on 2026-11-10."
    assert redact(text) == text


def test_marker_is_the_bracketed_type_name() -> None:
    assert {kind.marker for kind in PiiType} == {
        "[AADHAAR]",
        "[PAN]",
        "[IFSC]",
        "[MOBILE]",
        "[EMAIL]",
        "[BANK_ACCOUNT]",
    }


def test_redact_is_linear_on_adversarial_input() -> None:
    hostile = ("a" * 5000 + "@") * 20 + "9" * 50000 + "a." * 20000
    started = time.perf_counter()
    redact(hostile)
    assert time.perf_counter() - started < 5.0
