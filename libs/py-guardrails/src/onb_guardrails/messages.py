"""What the guards do to the messages going to the model and the reply coming back (PRD §8.1).

Input, per message (the caller's messages are never changed; new ones are built):
- the first system message gets `UNTRUSTED_NOTICE` (a system message holding only the notice is
  added when there is none);
- every message is untrusted unless it is a `SystemMessage`, an `AIMessage` (an earlier turn, already
  output-scanned) or a `trusted_message(...)`. Untrusted content (chat, tool results, any other role)
  is scanned for injection on its original text, PII-redacted, then delimited. A list-content block
  that is not text or binary (a `tool_result`, a `json` block, anything unknown) is redacted and turned
  into a delimited text block holding its JSON, so no shape slips past the scan;
- a `trusted_message` (the agent's own task instruction, the repair prompt) is scanned and redacted but
  not delimited. Trust is the message's class, which data cannot set: a message rebuilt from a dict or
  a checkpoint is a plain `HumanMessage` again, so it fails safe to untrusted. Never interpolate hire or
  tool text into a trusted message;
- system messages and earlier AI turns are PII-redacted.

Output: the reply's text, tool-call arguments, other content blocks and provider extras are redacted.
PII is counted once, from the text and the tool-call arguments (providers repeat tool calls in content
blocks and extras). Structural fields (`id`, `type`, `name`, `tool_call_id`, ...) are never rewritten, so
tool-call pairing survives. Binary blocks (images, files, audio) pass unchanged.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.function import FunctionMessage

from onb_guardrails.delimit import UNTRUSTED_NOTICE, delimit_untrusted
from onb_guardrails.injection import PATTERN_IDS, detect_injection
from onb_guardrails.pii import detect, redact

Content = str | list[Any]
Redactor = Callable[[str], str]

BINARY_BLOCK_TYPES: Final = frozenset({"image", "image_url", "file", "audio", "input_audio", "video"})
_STRUCTURAL_KEYS: Final = frozenset(
    {"type", "id", "name", "tool_use_id", "tool_call_id", "index", "mime_type", "source_type"}
)


class TrustedInstruction(HumanMessage):
    """A user-role message our own code wrote. Built only by `trusted_message`."""


def trusted_message(text: str) -> HumanMessage:
    """A user-role instruction written by our own code (task instruction, repair prompt): not delimited.

    `text` must come from code or reviewed templates, never from a hire, an upload or a tool result.
    """
    return TrustedInstruction(content=text)


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


def _counted_redactor(counter: Counter[str]) -> Redactor:
    def redact_and_count(text: str) -> str:
        counter.update(str(span.type) for span in detect(text))
        return redact(text)

    return redact_and_count


def _is_binary(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in BINARY_BLOCK_TYPES


def _is_text_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)


def _redact_tree(value: Any, fn: Redactor) -> Any:
    """`value` with `fn` applied to every string leaf, except under structural keys."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {key: item if key in _STRUCTURAL_KEYS else _redact_tree(item, fn) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_tree(item, fn) for item in value)
    return value


def _leaves(value: Any) -> list[str]:
    """Every string leaf of `value`, except under structural keys (what `_redact_tree` would touch)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() if key not in _STRUCTURAL_KEYS for text in _leaves(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _leaves(item)]
    return []


def _source(message: BaseMessage) -> str | None:
    """Where an untrusted message came from (`chat`, `tool`), `instruction` for a trusted one, or None."""
    if isinstance(message, TrustedInstruction):
        return "instruction"
    if isinstance(message, SystemMessage | AIMessage):
        return None
    if isinstance(message, ToolMessage | FunctionMessage):
        return "tool"
    return "chat"


@dataclass(frozen=True, slots=True)
class _Guarded:
    content: Content
    scanned: tuple[str, ...]
    """Original text that was scanned for injection."""
    redacted: tuple[str, ...]
    """The same text after redaction."""


def _untrusted(content: Content, source: str, redactor: Redactor) -> _Guarded:
    wrap = partial(delimit_untrusted, source=source)
    if isinstance(content, str):
        clean = redactor(content)
        return _Guarded(wrap(clean), (content,), (clean,))
    blocks: list[Any] = []
    scanned: list[str] = []
    redacted: list[str] = []
    for block in content:
        if _is_binary(block):
            blocks.append(block)
            continue
        if isinstance(block, str) or _is_text_block(block):
            text = block if isinstance(block, str) else block["text"]
            clean = redactor(text)
            blocks.append(wrap(clean) if isinstance(block, str) else {**block, "text": wrap(clean)})
        else:
            text = "\n".join(_leaves(block)) if isinstance(block, dict) else str(block)
            clean = json.dumps(_redact_tree(block, redactor), ensure_ascii=False, sort_keys=True, default=str)
            blocks.append({"type": "text", "text": wrap(clean)})
        scanned.append(text)
        redacted.append(clean)
    return _Guarded(blocks, tuple(scanned), tuple(redacted))


def _trusted(content: Content, redactor: Redactor) -> _Guarded:
    """Redacted in place, never delimited; the original text is returned for the injection scan."""
    if isinstance(content, str):
        return _Guarded(redactor(content), (content,), ())
    blocks = [block if _is_binary(block) else _redact_tree(block, redactor) for block in content]
    scanned = tuple(text for block in content if not _is_binary(block) for text in _leaves(block))
    return _Guarded(blocks, scanned, ())


def _with_notice(messages: list[BaseMessage]) -> list[BaseMessage]:
    for index, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            if UNTRUSTED_NOTICE in message.text:
                return messages
            content = _append_notice(message.content)
            return [*messages[:index], message.model_copy(update={"content": content}), *messages[index + 1 :]]
    return [SystemMessage(UNTRUSTED_NOTICE), *messages]


def _append_notice(content: Content) -> Content:
    if isinstance(content, str):
        return f"{content}\n\n{UNTRUSTED_NOTICE}" if content else UNTRUSTED_NOTICE
    return [*content, {"type": "text", "text": UNTRUSTED_NOTICE}]


def _fields(message: BaseMessage, redactor: Redactor) -> dict[str, Any]:
    """`name` and `additional_kwargs` redacted: providers can send both to the model."""
    fields: dict[str, Any] = {"additional_kwargs": _redact_tree(message.additional_kwargs, redactor)}
    return fields if message.name is None else {**fields, "name": redactor(message.name)}


def guard_input(messages: Iterable[BaseMessage]) -> InputScan:
    counter: Counter[str] = Counter()
    redactor = _counted_redactor(counter)
    patterns: set[str] = set()
    sources: list[str] = []
    untrusted: list[str] = []
    guarded: list[BaseMessage] = []
    for message in messages:
        source = _source(message)
        if isinstance(message, AIMessage):
            guarded.append(_redact_ai(message, redactor, redact))
            continue
        if source is None:
            update = {"content": _trusted(message.content, redactor).content, **_fields(message, redact)}
            guarded.append(message.model_copy(update=update))
            continue
        result = (
            _trusted(message.content, redactor)
            if source == "instruction"
            else _untrusted(message.content, source, redactor)
        )
        found = {pattern for text in result.scanned for pattern in detect_injection(text)}
        if found:
            patterns |= found
            sources.append(source)
        untrusted.extend(result.redacted)
        update = {"content": result.content, **_fields(message, redact)}
        guarded.append(message.model_copy(update=update))
    return InputScan(
        messages=tuple(_with_notice(guarded)),
        pii_counts=tuple(sorted(counter.items())),
        injection_patterns=tuple(name for name in PATTERN_IDS if name in patterns),
        injection_sources=tuple(dict.fromkeys(sources)),
        untrusted_texts=tuple(untrusted),
    )


def _redact_ai_content(content: Content, counted: Redactor, plain: Redactor) -> Content:
    if isinstance(content, str):
        return counted(content)
    return [
        counted(block)
        if isinstance(block, str)
        else block
        if _is_binary(block)
        else _redact_tree(block, counted if _is_text_block(block) else plain)
        for block in content
    ]


def _redact_ai(message: AIMessage, counted: Redactor, plain: Redactor) -> AIMessage:
    """`message` redacted; PII is counted from its text and tool-call arguments only."""
    return message.model_copy(
        update={
            "content": _redact_ai_content(message.content, counted, plain),
            "tool_calls": [{**call, "args": _redact_tree(call["args"], counted)} for call in message.tool_calls],
            "invalid_tool_calls": _redact_tree(message.invalid_tool_calls, plain),
            "additional_kwargs": _redact_tree(message.additional_kwargs, plain),
        }
    )


def scan_output(message: AIMessage) -> tuple[AIMessage, tuple[tuple[str, int], ...]]:
    """The redacted reply and the PII types found in it, with counts."""
    counter: Counter[str] = Counter()
    return _redact_ai(message, _counted_redactor(counter), redact), tuple(sorted(counter.items()))


def output_texts(message: AIMessage) -> tuple[str, ...]:
    """What an external guard checks on a reply: its text and each tool call's arguments as JSON."""
    calls = (
        json.dumps({"name": call["name"], "args": call["args"]}, ensure_ascii=False, sort_keys=True, default=str)
        for call in message.tool_calls
    )
    return tuple(text for text in (message.text, *calls) if text)
