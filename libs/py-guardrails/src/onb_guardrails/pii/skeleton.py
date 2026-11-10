"""Matching skeleton: a normalised copy of the text with a map back to original offsets.

Detectors match on the skeleton so formatting tricks hide nothing, and spans are mapped back so
redaction removes the original characters. Per character:
- format and combining marks (zero-width space, soft hyphen, joiners, accents) are dropped;
- NFKC folds full-width, superscript and circled forms (``\uff21`` -> ``A``, ``\u2468`` -> ``9``, ``\uff20`` -> ``@``);
- any remaining Unicode decimal digit becomes its ASCII digit (Devanagari ``\u096f`` -> ``9``);
- any Unicode space becomes an ASCII space;
- Cyrillic and Greek letters that look like Latin ones become those Latin letters.
"""

import unicodedata
from dataclasses import dataclass
from typing import Final

_DROPPED_CATEGORIES: Final = frozenset({"Cf", "Mn", "Me"})
# Blank-looking characters that are not Unicode spaces: Hangul fillers and the Braille blank.
_BLANKS: Final = frozenset("\u115f\u1160\u3164\uffa0\u2800")

# Cyrillic and Greek homoglyphs of Latin letters (upper and lower case).
_CONFUSABLES: Final = {
    **dict(
        zip(
            "\u0410\u0412\u0421\u0415\u041d\u0406\u0408\u041a\u041c\u041e\u0420\u0405\u0422\u0425\u0423",
            "ABCEHIJKMOPSTXY",
            strict=True,
        )
    ),
    **dict(zip("\u0430\u0435\u0456\u0458\u043e\u0440\u0441\u0455\u0445\u0443", "aeijopcsxy", strict=True)),
    **dict(
        zip(
            "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7",
            "ABEZHIKMNOPTYX",
            strict=True,
        )
    ),
    **dict(zip("\u03bf\u03ba\u03bd", "okv", strict=True)),
}


@dataclass(frozen=True, slots=True)
class Skeleton:
    """``text`` is the normalised copy; ``origin[i]`` is the original index of ``text[i]``."""

    text: str
    origin: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """Original ``[start, end)`` covering skeleton ``[start, end)`` (``end > start``)."""
        return self.origin[start], self.origin[end - 1] + 1


def _fold(char: str) -> str:
    if unicodedata.category(char) == "Nd":
        return str(unicodedata.decimal(char))
    if char.isspace() or char in _BLANKS:
        return " "
    return _CONFUSABLES.get(char, char)


def skeleton(text: str) -> Skeleton:
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if char.isascii():
            chars.append(char)
            origin.append(index)
            continue
        if unicodedata.category(char) in _DROPPED_CATEGORIES:
            continue
        for part in unicodedata.normalize("NFKC", char):
            if unicodedata.category(part) in _DROPPED_CATEGORIES:
                continue
            for folded in _fold(part):
                chars.append(folded)
                origin.append(index)
    return Skeleton("".join(chars), tuple(origin))
