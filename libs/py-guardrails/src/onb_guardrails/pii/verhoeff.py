"""Verhoeff checksum, the check digit scheme of the Aadhaar number (PRD §7.10)."""

from typing import Final

# Multiplication table of the dihedral group D5.
_D: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
# Position-dependent permutation table: row i is the base permutation applied i times. Deriving the
# rows (instead of transcribing all eight) removes a whole class of copy errors.
_BASE_PERMUTATION: Final = (1, 5, 7, 6, 2, 8, 3, 0, 9, 4)


def _permutation_rows() -> tuple[tuple[int, ...], ...]:
    rows = [tuple(range(10))]
    for _ in range(7):
        rows.append(tuple(_BASE_PERMUTATION[digit] for digit in rows[-1]))
    return tuple(rows)


_P: Final = _permutation_rows()
_INV: Final = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
_ASCII_DIGITS: Final = frozenset("0123456789")


def _is_ascii_digits(number: str) -> bool:
    return bool(number) and set(number) <= _ASCII_DIGITS


def _checksum(number: str, offset: int) -> int:
    check = 0
    for position, char in enumerate(reversed(number)):
        check = _D[check][_P[(position + offset) % 8][int(char)]]
    return check


def verhoeff_is_valid(number: str) -> bool:
    """True when ``number`` is ASCII digits whose last digit is a correct Verhoeff check digit."""
    return _is_ascii_digits(number) and _checksum(number, 0) == 0


def verhoeff_check_digit(payload: str) -> str:
    """The Verhoeff check digit to append to ``payload``."""
    if not _is_ascii_digits(payload):
        raise ValueError("Verhoeff input must be ASCII digits")
    return str(_INV[_checksum(payload, 1)])
