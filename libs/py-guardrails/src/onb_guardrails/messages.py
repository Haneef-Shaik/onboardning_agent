"""What the guards do to the messages going to the model and the reply coming back (PRD §8.1).

Input, per message (the caller's messages are never changed; new ones are built):
- the first system message gets `UNTRUSTED_NOTICE` (a system message holding only the notice is
  added when there is none);
- untrusted messages (chat: `HumanMessage`; tool results: `ToolMessage`) are scanned for injection
  on their original text, then PII-redacted, then delimited; a `trusted_message(...)` (the agent's own
  task instruction, the repair prompt) is redacted but neither scanned nor delimited;
- every other message (system, earlier AI turns with their tool calls) is PII-redacted.

Output: every string in the reply's content, tool-call arguments and provider extras is redacted.
Only text blocks of input content are touched, so images and other binary blocks pass unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from onb_guardrails.delimit import UNTRUSTED_NOTICE, delimit_untrusted
from onb_guardrails.injection import PATTERN_IDS, detect_injection
from onb_guardrails.pii import detect, redact

TRUSTED_KWARG: Final = "onb_guardrails_trusted"


def trusted_message(text: str) -> HumanMessage:
    """A user-role message written by our own code (task instruction, repair prompt): not delimited."""
    return HumanMessage(content=text, additional_kwargs={TRUSTED_KWARG: True})


@dataclass(frozen=True, slots=True)
class InputScan:
    messages: tuple[BaseMessage, ...]
    pii_counts: tuple[tuple[str, int], ...]
    injection_patterns: tuple[str, ...]
    injection_sources: tuple[str, ...]
    untrusted_texts: tuple[str, ...]
    """The untrusted text after redaction, for an external guard."""


def pii_reason(counts: Iterable[tuple[str, int]]) -> str:
    return ",".join(f"{kind}={count}" for kind, count in counts) or "redacted"


def _counted_redactor(counter: Counter[str]) -> Callable[[str], str]:
    def redact_and_count(text: str) -> str:
        counter.update(str(span.type) for span in detect(text))
        return redact(text)

    return redact_and_count


def _map_text(content: str | list[Any], fn: Callable[[str], str]) -> str | list[Any]:
    """`content` with `fn` applied to its text: the string, or each string / text block of a list."""
    if isinstance(content, str):
        return fn(content)
    mapped: list[Any] = []
    for block in content:
        if isinstance(block, str):
            mapped.append(fn(block))
        elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            mapped.append({**block, "text": fn(block["text"])})
        else:
            mapped.append(block)
    return mapped


def _texts(content: str | list[Any]) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [
        block if isinstance(block, str) else block["text"]
        for block in content
        if isinstance(block, str)
        or (isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
    ]


def _redact_tree(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {key: _redact_tree(item, fn) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_tree(item, fn) for item in value)
    return value


def _delimit(text: str, *, source: str) -> str:
    return delimit_untrusted(text, source)


def _source(message: BaseMessage) -> str | None:
    """`chat` or `tool` for an untrusted message, None for a trusted one."""
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, HumanMessage) and not message.additional_kwargs.get(TRUSTED_KWARG):
        return "chat"
    return None


def _with_notice(messages: list[BaseMessage]) -> list[BaseMessage]:
    for index, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            if UNTRUSTED_NOTICE in message.text:
                return messages
            content = _append_notice(message.content)
            return [*messages[:index], message.model_copy(update={"content": content}), *messages[index + 1 :]]
    return [SystemMessage(UNTRUSTED_NOTICE), *messages]


def _append_notice(content: str | list[Any]) -> str | list[Any]:
    if isinstance(content, str):
        return f"{content}\n\n{UNTRUSTED_NOTICE}" if content else UNTRUSTED_NOTICE
    return [*content, {"type": "text", "text": UNTRUSTED_NOTICE}]


def guard_input(messages: Iterable[BaseMessage]) -> InputScan:
    counter: Counter[str] = Counter()
    redactor = _counted_redactor(counter)
    patterns: list[str] = []
    sources: list[str] = []
    untrusted: list[str] = []
    guarded: list[BaseMessage] = []
    for message in messages:
        source = _source(message)
        if source is not None:
            found = [pattern for text in _texts(message.content) for pattern in detect_injection(text)]
            if found:
                patterns.extend(found)
                sources.append(source)
            redacted = _map_text(message.content, redactor)
            untrusted.extend(_texts(redacted))
            content = _map_text(redacted, partial(_delimit, source=source))
            guarded.append(message.model_copy(update={"content": content}))
        elif isinstance(message, AIMessage):
            guarded.append(guard_output(message, redactor))
        else:
            guarded.append(message.model_copy(update={"content": _map_text(message.content, redactor)}))
    return InputScan(
        messages=tuple(_with_notice(guarded)),
        pii_counts=tuple(sorted(counter.items())),
        injection_patterns=tuple(name for name in PATTERN_IDS if name in patterns),
        injection_sources=tuple(dict.fromkeys(sources)),
        untrusted_texts=tuple(untrusted),
    )


def guard_output(message: AIMessage, redactor: Callable[[str], str]) -> AIMessage:
    """`message` with every string in its content, tool calls and provider extras redacted."""
    return message.model_copy(
        update={
            "content": _redact_tree(message.content, redactor),
            "tool_calls": _redact_tree(message.tool_calls, redactor),
            "invalid_tool_calls": _redact_tree(message.invalid_tool_calls, redactor),
            "additional_kwargs": _redact_tree(message.additional_kwargs, redactor),
        }
    )


def scan_output(message: AIMessage) -> tuple[AIMessage, tuple[tuple[str, int], ...]]:
    """The redacted reply and the PII types found in it, with counts."""
    counter: Counter[str] = Counter()
    return guard_output(message, _counted_redactor(counter)), tuple(sorted(counter.items()))
