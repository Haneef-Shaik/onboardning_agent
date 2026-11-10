"""Property tests: whatever text goes in, redact() output contains no detector hit (card PII-3 box 4).

Inputs mix arbitrary Unicode, digit/letter noise shaped to provoke near-misses, and generated synthetic
PII values in every layout the detectors accept, glued together with random separators.
"""

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from onb_guardrails.pii import PiiType, detect, redact, verhoeff_check_digit

_DIGITS = "0123456789"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SEPARATORS = st.sampled_from(["", " ", "-", ".", ",", "@", "/", "\n", "\t", ":", "+", "_", "(", ")", "[", "]"])


@st.composite
def _aadhaar(draw: st.DrawFn) -> str:
    payload = draw(st.sampled_from("23456789")) + draw(st.text(_DIGITS, min_size=10, max_size=10))
    number = payload + verhoeff_check_digit(payload)
    sep = draw(st.sampled_from(["", " ", "-"]))
    return sep.join((number[:4], number[4:8], number[8:]))


@st.composite
def _mobile(draw: st.DrawFn) -> str:
    number = draw(st.sampled_from("6789")) + draw(st.text(_DIGITS, min_size=9, max_size=9))
    prefix = draw(st.sampled_from(["", "+91", "+91 ", "+91-", "91", "0"]))
    sep = draw(st.sampled_from(["", " ", "-"]))
    return prefix + number[:5] + sep + number[5:]


def _pan() -> st.SearchStrategy[str]:
    return st.builds(
        lambda a, d, z, lower: (a + d + z).lower() if lower else a + d + z,
        st.text(_UPPER, min_size=5, max_size=5),
        st.text(_DIGITS, min_size=4, max_size=4),
        st.text(_UPPER, min_size=1, max_size=1),
        st.booleans(),
    )


def _ifsc() -> st.SearchStrategy[str]:
    return st.builds(
        lambda a, b: a + "0" + b,
        st.text(_UPPER, min_size=4, max_size=4),
        st.text(_UPPER + _DIGITS, min_size=6, max_size=6),
    )


def _email() -> st.SearchStrategy[str]:
    label = st.text("abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8)
    return st.builds(
        lambda local, labels, tld: f"{local}@{'.'.join(labels)}.{tld}",
        st.text("abcdefghijklmnopqrstuvwxyz0123456789._+-", min_size=1, max_size=12),
        st.lists(label, min_size=1, max_size=3),
        st.text("abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=4),
    )


def _bank() -> st.SearchStrategy[str]:
    return st.builds(
        lambda keyword, digits: f"{keyword}{digits}",
        st.sampled_from(["account number ", "A/c no: ", "acct # ", "Account No. ", "acc "]),
        st.text(_DIGITS, min_size=9, max_size=18),
    )


_PII = st.one_of(_aadhaar(), _mobile(), _pan(), _ifsc(), _email(), _bank())
_NOISE = st.one_of(
    st.text(max_size=20),
    st.text(_DIGITS + " -", max_size=20),
    st.text(_UPPER + _DIGITS + "abc", max_size=14),
)
_FRAGMENT = st.one_of(_PII, _NOISE)


@st.composite
def _documents(draw: st.DrawFn) -> str:
    fragments = draw(st.lists(_FRAGMENT, max_size=12))
    return "".join(fragment + draw(_SEPARATORS) for fragment in fragments)


_SETTINGS = settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_SETTINGS
@given(_documents())
@example("Aadhaar 917219206868 / +91 90000 00001 / abcde1234f@x.co")
@example("9000000002" * 3)
@example("acc 123456789 9000000002@example.com")
@example("account number 000000000AAAAA0000A")  # one pass exposes the glued PAN
def test_redact_output_contains_no_detector_hit(text: str) -> None:
    assert detect(redact(text)) == ()


@_SETTINGS
@given(_documents())
def test_redact_is_idempotent(text: str) -> None:
    once = redact(text)
    assert redact(once) == once


@_SETTINGS
@given(_PII, _NOISE.filter(lambda s: not detect(s)))
def test_every_generated_value_is_redacted_out_of_its_context(value: str, context: str) -> None:
    text = f"{context} {value} {context}"
    redacted = redact(text)
    assert value not in redacted
    assert any(kind.marker in redacted for kind in PiiType)


@_SETTINGS
@given(st.text(alphabet=st.characters(exclude_characters=_DIGITS + "@"), max_size=200))
def test_text_without_digits_or_at_signs_is_left_unchanged(text: str) -> None:
    # Every detector needs an ASCII or Unicode digit, or an @; no-digit text must come back unchanged.
    if not any(ch.isdecimal() for ch in text):
        assert redact(text) == text
