"""Property tests for redact() (card PII-3 box 4: its output contains no detector hit).

``detect(redact(x)) == ()`` is the card's property. Because redact() repeats until nothing is detected,
the property alone cannot see a value that was only partly redacted. So the stronger properties below
also check that no character of a generated value survives:
- values are glued to their context with or without separators;
- values are obfuscated with zero-width characters, soft hyphens and Unicode spaces;
- the output must contain no digit (any script) and no ``@`` (any width) the context did not supply.
"""

import unicodedata

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from onb_guardrails.pii import detect, redact, verhoeff_check_digit

_DIGITS = "0123456789"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LETTERS = _UPPER + _UPPER.lower()
_SEPARATORS = st.sampled_from(["", " ", "-", ".", ",", "@", "/", "\n", "\t", ":", "+", "_", "(", ")", "[", "]"])
_INVISIBLE = ["\u200b", "\u200c", "\u200d", "\u00ad", "\u2060", "\ufeff"]


@st.composite
def _aadhaar(draw: st.DrawFn) -> str:
    payload = draw(st.sampled_from("23456789")) + draw(st.text(_DIGITS, min_size=10, max_size=10))
    number = payload + verhoeff_check_digit(payload)
    first, second = draw(st.sampled_from(["", " ", "-", ".", "\u00a0", "  "])), draw(st.sampled_from(["", " ", "-"]))
    return f"{number[:4]}{first}{number[4:8]}{second}{number[8:]}"


@st.composite
def _mobile(draw: st.DrawFn) -> str:
    n = draw(st.sampled_from("6789")) + draw(st.text(_DIGITS, min_size=9, max_size=9))
    prefix = draw(st.sampled_from(["", "+91", "+91 ", "+91-", "91", "0", "+91 ("]))
    sep = draw(st.sampled_from([" ", "-", ".", "\u00a0"]))
    layout = draw(
        st.sampled_from(
            [
                n,
                n[:5] + sep + n[5:],
                n[:3] + sep + n[3:6] + sep + n[6:],
                sep.join(n[i : i + 2] for i in range(0, 10, 2)),
            ]
        )
    )
    return prefix + layout


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
        st.text("abcdefghijklmnopqrstuvwxyz0123456789._+-", min_size=1, max_size=80),
        st.lists(label, min_size=1, max_size=3),
        st.text("abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=4),
    )


def _bank() -> st.SearchStrategy[str]:
    return st.builds(
        lambda keyword, digits: f"{keyword}{digits}",
        st.sampled_from(["account number ", "A/c no: ", "acct # ", "Account No. ", "acc ", "account no is "]),
        st.text(_DIGITS, min_size=9, max_size=18),
    )


@st.composite
def _obfuscated(draw: st.DrawFn, values: st.SearchStrategy[str]) -> str:
    value = draw(values)
    marks = draw(st.lists(st.sampled_from(["", "", "", *_INVISIBLE]), min_size=len(value), max_size=len(value)))
    return "".join(char + mark for char, mark in zip(value, marks, strict=True))


_PII = st.one_of(_aadhaar(), _mobile(), _pan(), _ifsc(), _email(), _bank())
_DIGIT_PII = st.one_of(_aadhaar(), _mobile(), _email())
_NOISE = st.one_of(
    st.text(max_size=20),
    st.text(_DIGITS + " -", max_size=20),
    st.text(_UPPER + _DIGITS + "abc", max_size=14),
)


@st.composite
def _documents(draw: st.DrawFn) -> str:
    fragments = draw(st.lists(st.one_of(_PII, _obfuscated(_PII), _NOISE), max_size=12))
    return "".join(fragment + draw(_SEPARATORS) for fragment in fragments)


def _is_digit_or_at(char: str) -> bool:
    return any(part.isdecimal() or part == "@" for part in unicodedata.normalize("NFKC", char))


def _folds_to_alnum_or_at(char: str) -> bool:
    return any(part.isalnum() or part == "@" for part in unicodedata.normalize("NFKC", char))


def _leaked(text: str) -> list[str]:
    return [char for char in text if _is_digit_or_at(char)]


# Context made of punctuation, symbols and spaces only: no letters, digits or @ in any script, even after NFKC
# (a circled A folds to "A"; a PAN glued to a preceding letter is a documented residual risk).
_PUNCT_CONTEXT = st.text(
    alphabet=st.characters(categories=("P", "S", "Zs"), exclude_characters="@%").filter(
        lambda c: not _folds_to_alnum_or_at(c)
    ),
    max_size=8,
)
_GLUE = st.sampled_from(["", " ", "-", ".", ",", "/", "\n", ":", "(", ")", "\u00a0"])

_SETTINGS = settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_SETTINGS
@given(_documents())
@example("Aadhaar 917000000000 / +91 90000 00001 / abcde1234f@x.co")
@example("9000000002" * 3)
@example("acc 123456789 9000000002@example.com")
@example("account number 000000000AAAAA0000A")  # one pass exposes the glued PAN
@example("200000000009AAAAA0000A")  # found by hypothesis: Aadhaar glued to a PAN
def test_redact_output_contains_no_detector_hit(text: str) -> None:
    assert detect(redact(text)) == ()


@_SETTINGS
@given(_documents())
def test_redact_is_idempotent(text: str) -> None:
    once = redact(text)
    assert redact(once) == once


@_SETTINGS
@given(st.one_of(_PII, _obfuscated(_PII)), _PUNCT_CONTEXT, _GLUE, _PUNCT_CONTEXT, _GLUE)
@example("90000 00001", "", "", "", "")
@example("jane@example.com", "", "", "-call", "")
@example("x" * 80 + "@example.com", "", "", "", "")
def test_no_character_of_a_value_survives_in_any_glue(
    value: str, before: str, glue1: str, after: str, glue2: str
) -> None:
    redacted = redact(f"{before}{glue1}{value}{glue2}{after}")
    assert _leaked(redacted) == []


@_SETTINGS
@given(_obfuscated(_DIGIT_PII), st.text(_LETTERS, max_size=6), st.text(_LETTERS, max_size=6))
@example("90000 00001", "", "jane@example.com")
def test_digit_values_glued_to_letters_leave_nothing_behind(value: str, before: str, after: str) -> None:
    redacted = redact(f"{before}{value}{after}")
    assert _leaked(redacted) == []


@_SETTINGS
@given(st.text(alphabet=st.characters().filter(lambda c: not _is_digit_or_at(c)), max_size=200))
def test_text_without_digits_or_at_signs_is_left_unchanged(text: str) -> None:
    # Every detector needs a digit or an @ (after NFKC folding), so such text must come back unchanged.
    assert redact(text) == text
