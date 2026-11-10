"""Free-text PII detectors and redact() (PRD \u00a77.10 Free-text input). All values are synthetic."""

import time

import pytest
from onb_guardrails.pii import PiiSpan, PiiType, detect, redact


def _found(text: str) -> list[tuple[PiiType, str]]:
    return [(span.type, text[span.start : span.end]) for span in detect(text)]


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Aadhaar 9999 0000 0125 ok", PiiType.AADHAAR, "9999 0000 0125"),
        ("uid:999912345678.", PiiType.AADHAAR, "999912345678"),
        ("UID 9999-5555-0005", PiiType.AADHAAR, "9999-5555-0005"),
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
    assert detect("Order 9999 0000 0126 shipped") == ()


def test_aadhaar_starting_with_0_or_1_is_not_detected() -> None:
    assert detect("id 1234 5678 9010 and 023456789014") == ()


def test_mobile_inside_a_12_digit_aadhaar_is_reported_as_aadhaar_only() -> None:
    """Card trap: 917000000000 is Aadhaar-valid and also reads as 91 + a mobile number."""
    assert _found("Aadhaar 917000000000") == [(PiiType.AADHAAR, "917000000000")]
    assert _found("Aadhaar 9170 0000 0000") == [(PiiType.AADHAAR, "9170 0000 0000")]
    assert redact("Aadhaar 917000000000") == "Aadhaar [AADHAAR]"


def test_mobile_is_not_found_inside_a_longer_digit_run() -> None:
    assert detect("AWB 77190000000021") == ()


def test_aadhaar_after_a_failing_group_is_still_found() -> None:
    assert _found("1111 9999 0000 0125") == [(PiiType.AADHAAR, "9999 0000 0125")]


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
    text = "Order 848589512865, pincode 560001, \u20b91,23,456 on 2026-11-10."
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


def _full_width(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


ZWSP, SOFT_HYPHEN, NBSP = "\u200b", "\u00ad", "\u00a0"
CYRILLIC_PAN = "\u0410\u0412\u0421D\u04151234F"  # Cyrillic A, V(B), S(C), then Latin D, Cyrillic E, 1234F
SUPERSCRIPT_MOBILE = "9000000001".translate({ord("0"): "\u2070", ord("1"): "\u00b9", ord("9"): "\u2079"})
CIRCLED_MOBILE = "\u2468" + "\u24ea" * 8 + "\u2460"  # circled 9, circled 0 x8, circled 1


# Review findings (security and code review of attempt 1): formatting tricks that slipped past redact().
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"PAN ABCDE{ZWSP}1234F", "PAN [PAN]"),
        (f"PAN ABCDE{SOFT_HYPHEN}1234F", "PAN [PAN]"),
        ("id " + ZWSP.join("999900000125"), "id [AADHAAR]"),
        (f"id 9999{NBSP}0000{NBSP}0125", "id [AADHAAR]"),
        ("id 9999.0000.0125", "id [AADHAAR]"),
        ("id 9999 0000-0125", "id [AADHAAR]"),
        ("id 9999  0000  0125", "id [AADHAAR]"),
        ("id 9999\n0000\n0125", "id [AADHAAR]"),
        (f"call 90000{NBSP}00001", "call [MOBILE]"),
        (f"call 90000{ZWSP}00001", "call [MOBILE]"),
        ("call 90000.00001", "call [MOBILE]"),
        ("call 900 000 0001", "call [MOBILE]"),
        ("call 90 00 00 00 01", "call [MOBILE]"),
        ("call +91 (900) 000-0001", "call [MOBILE]"),
        (f"call {SUPERSCRIPT_MOBILE}", "call [MOBILE]"),
        (f"call {CIRCLED_MOBILE}", "call [MOBILE]"),
        (f"PAN {_full_width('ABCDE1234F')}", "PAN [PAN]"),
        (f"PAN {CYRILLIC_PAN}", "PAN [PAN]"),
        ("PAN ABCDE 1234 F", "PAN [PAN]"),
        ("PAN:ABCDE1234Fthanks", "PAN:[PAN]thanks"),
        ("mail jane\uff20example.com", "mail [EMAIL]"),
        ("mail jane@ex\u00e4mple.com", "mail [EMAIL]"),
        ("mail jane%40example.com", "mail [EMAIL]"),
        ("mail j\u00f6hn.doe@example.com", "mail [EMAIL]"),
        ("jane@example.com-call", "[EMAIL]-call"),
        ("x" * 80 + "@example.com", "[EMAIL]"),
        ("account no 9999 0000 0126", "account no [BANK_ACCOUNT]"),
    ],
)
def test_formatting_tricks_do_not_hide_a_value(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_overlapping_values_are_redacted_as_their_union() -> None:
    """Review finding: dropping the shorter overlapping span left half a mobile in the output."""
    assert redact("90000 00001jane@example.com") == "[EMAIL]"
    assert redact("tel 90000 00001jane@example.com ok") == "tel [EMAIL] ok"


@pytest.mark.parametrize(
    "hostile",
    [
        "account" + " " * 100_000 + "x",
        "acct\t" * 20_000,
        "ABCDE1234F " * 20_000,
        "9" * 200_000,
        "1 " * 100_000,
    ],
    ids=["account-spaces", "acct-tabs", "many-pans", "digit-run", "spaced-digits"],
)
def test_redact_stays_linear_on_review_inputs(hostile: str) -> None:
    started = time.perf_counter()
    redact(hostile)
    assert time.perf_counter() - started < 5.0
