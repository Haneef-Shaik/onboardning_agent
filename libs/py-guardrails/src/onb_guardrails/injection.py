"""Heuristic prompt-injection detector for connectors without a provider-side filter (PRD §8.1 Input).

`detect_injection(text)` returns the ids of the known attack patterns found in `text`, in
`PATTERN_IDS` order (empty when none). Matching runs on a normalised copy so formatting tricks hide
nothing: the PII skeleton (zero-width and combining marks dropped, NFKC, Cyrillic/Greek lookalikes
folded), lower case, and letters spaced out one by one (``i g n o r e``) joined back up.

It is a heuristic: it catches the known phrasings in `tests/injection/corpus.jsonl` and quotes of
them in a harmless context count as hits. It is one layer; untrusted content is also delimited
(`onb_guardrails.delimit`) and Bedrock Guardrails add the provider's filter when configured.
"""

from __future__ import annotations

import re
from typing import Final

from onb_guardrails.delimit import DELIMITER_TAG
from onb_guardrails.pii.skeleton import skeleton

_INSTRUCTION_WORDS = r"(?:instructions?|rules|guidelines|directives|prompts?|polic(?:y|ies)|programming)"
_FLAGS = re.IGNORECASE | re.MULTILINE

# Pattern id -> regex over the normalised text (spaces collapsed, newlines kept).
_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    name: re.compile(regex, _FLAGS)
    for name, regex in {
        "ignore_instructions": rf"\bignore\b[^\n]{{0,40}}?\b{_INSTRUCTION_WORDS}\b",
        "disregard_instructions": rf"\bdisregard\b[^\n]{{0,40}}?\b(?:{_INSTRUCTION_WORDS}|above|previous|prior)\b",
        "forget_instructions": rf"\bforget\b[^\n]{{0,30}}?\b(?:everything|{_INSTRUCTION_WORDS}|what you were told)\b",
        "role_override": r"\byou are now (?:an? |the )?(?:admin|administrator|root|superuser|hr_admin|developer"
        r"|unrestricted|unfiltered|jailbroken|dan|god)\b",
        "from_now_on": r"\bfrom now on,? you (?:will|must|shall|are|should)\b",
        "pretend_unrestricted": r"\b(?:pretend|imagine|roleplay|role-play|act)\b[^\n]{0,20}?\b(?:you are|to be|as)\b"
        r"[^\n]{0,40}?\b(?:unrestricted|unfiltered|jailbroken|evil|no (?:rules|restrictions|policies|limits|filters)"
        r"|without (?:rules|restrictions|limits))\b",
        "dan_jailbreak": r"\bdo anything now\b|\bact as dan\b|\bdan mode\b",
        "developer_mode": r"\b(?:developer|debug|god|sudo) mode\b[^\n]{0,20}?\b(?:enabled|on|activated)\b"
        r"|\benable (?:developer|debug|god|sudo) mode\b",
        "jailbreak_keyword": r"\bjailbr(?:eak|oken)\b",
        "prompt_leak": r"\b(?:reveal|print|show|output|display|tell me|share|leak|dump)\b[^\n]{0,30}?"
        r"\b(?:system prompt|(?:initial|hidden|original|secret) instructions|your instructions|your prompt)\b"
        r"|\bwhat (?:were|are) your (?:initial |original |system |hidden )?(?:instructions|prompt)\b",
        "repeat_above": r"\brepeat (?:the|all|everything)(?: text| words)? (?:above|before)\b",
        "fake_role_tag": r"<[ \t]{0,3}/?[ \t]{0,3}(?:system|assistant|developer|admin)[ \t]{0,3}>"
        r"|^[ \t]*#{1,6}[ \t]*(?:system|admin|developer)[ \t]*:|\[[ \t]*(?:system|admin)[ \t]*\]",
        "chat_template_token": r"<\|(?:im_start|im_end|system|endoftext|eot_id|start_header_id)\|>"
        r"|\[/?inst\]|<</?sys>>",
        "turn_injection": r"\n[ \t]*(?:human|assistant|system)[ \t]*:",
        "delimiter_escape": DELIMITER_TAG,
        "new_instructions": r"\b(?:new|updated|revised|real|actual)"
        r" (?:instructions|rules|directives|system prompt)\s*:",
        "bypass_safety": r"\b(?:override|bypass|disable|ignore|circumvent|turn off|skip)\b[^\n]{0,20}?"
        r"\b(?:safety|guardrails?|filters?|restrictions|content polic(?:y|ies)|security checks?|moderation)\b",
        "authority_claim": r"\b(?:i am|i'm|this is) (?:your|the) (?:developer|creator|administrator|admin|owner"
        r"|system administrator)\b",
        "admin_override": r"\b(?:admin|administrator|root|system|emergency) override\b",
        "encoded_payload": r"\b(?:decode|decrypt|deobfuscate)\b[^\n]{0,40}?\b(?:base64|rot13|hex|encoded)\b[^\n]{0,40}?"
        r"\b(?:execute|run|follow|obey)\b",
        "markdown_exfiltration": r"!\[[^\]\n]{0,200}\]\([ \t]*https?://[^)\s]{0,2000}?[?&][a-z_]+=",
        "data_exfiltration": r"\b(?:send|post|upload|forward|email|transmit|exfiltrate)\b[^\n]{0,50}?"
        r"\b(?:conversation|history|chat|prompt|context|records|secrets|credentials|passwords|tokens)\b[^\n]{0,40}?"
        r"\b(?:to|at)\s+https?://",
        "tool_hijack": r"\b(?:call|invoke|run|execute|use) (?:the )?(?:tool|function)\b[^\n]{0,40}?\b(?:with|using)\b"
        r"|\bexecute (?:the following|this) (?:command|code|query|tool call)\b",
        "conceal_instruction": r"\b(?:do not|don't|never) (?:tell|inform|alert|mention (?:it |this )?to)"
        r" (?:the user|anyone|the human)\b",
    }.items()
}

PATTERN_IDS: Final[tuple[str, ...]] = tuple(_PATTERNS)

# Three or more single letters each followed by one space or punctuation mark, e.g. "i g n o r e".
_SPACED_LETTERS: Final = re.compile(r"(?<![a-z])(?:[a-z][ .\-_*]){2,}[a-z](?![a-z])")
_RUNS_OF_BLANKS: Final = re.compile(r"[^\S\n]+")


def _join_spaced(match: re.Match[str]) -> str:
    return match.group(0)[::2]


def normalise(text: str) -> str:
    """The text the patterns match on: skeleton, lower case, spaced letters joined, blanks collapsed."""
    folded = skeleton(text).text.lower()
    joined = _SPACED_LETTERS.sub(_join_spaced, folded)
    return _RUNS_OF_BLANKS.sub(" ", joined)


def detect_injection(text: str) -> tuple[str, ...]:
    """Ids of the known injection patterns found in `text`, in `PATTERN_IDS` order."""
    normalised = normalise(text)
    return tuple(name for name, pattern in _PATTERNS.items() if pattern.search(normalised))
