"""The `when` condition language for template tasks (PRD §5.1, §6.1), parsed without `eval`.

    when: "role == 'engineer'"
    when: "role in ['engineer', 'designer'] and not remote"

Grammar (a closed set; anything else is refused):

    expr    := or
    or      := and ('or' and)*
    and     := not ('and' not)*
    not     := 'not' not | compare
    compare := operand (op operand | ('in' | 'not' 'in') list)?      one comparison, no chains
    operand := NAME | literal | '(' expr ')'
    list    := '[' (literal (',' literal)* ','?)? ']'
    literal := 'single' | "double" (no backslashes) | integer | true | false | null

A NAME is a template variable. Before parsing, a scan refuses, by name: attribute access (`a.b`),
function calls (`f(x)`, `(...)(...)`), subscripts (`a[0]`), imports (`import`, `from`, `__import__`,
`importlib`) and dangerous or dunder names (`eval`, `lambda`, `__class__`). Error messages carry the
reason and a position, never the condition text, which may hold anything a template author typed.

Evaluation walks the tree. It is strict and fails closed (`ExpressionReason.EVALUATION`): a missing
variable, a value of an unsupported type, ordering across types and a non-boolean condition are errors,
never silently false. `True == 1` is false here: booleans and numbers are different kinds.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, cast

__all__ = [
    "MAX_DEPTH",
    "MAX_LENGTH",
    "ExpressionError",
    "ExpressionReason",
    "Literal",
    "Value",
    "WhenExpression",
    "parse_when",
]

MAX_LENGTH: Final = 512
MAX_DEPTH: Final = 32

Literal = str | int | bool | None
Value = str | int | float | bool | None

_IMPORT_NAMES: Final = frozenset({"import", "from", "__import__", "importlib"})
_FORBIDDEN_NAMES: Final = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "lambda",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "input",
        "breakpoint",
        "builtins",
        "os",
        "sys",
        "subprocess",
    }
)
_CONSTANTS: Final[Mapping[str, Literal]] = {
    "true": True,
    "True": True,
    "false": False,
    "False": False,
    "null": None,
    "None": None,
}
_KEYWORDS: Final = frozenset({"and", "or", "not", "in"})
_ORDER_OPS: Final[Mapping[str, Callable[[Any, Any], Any]]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_TOKEN: Final = re.compile(
    r"""(?P<ws>\s+)
      | (?P<str>'[^'\\\n]*'|"[^"\\\n]*")
      | (?P<num>-?\d+)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>==|!=|<=|>=|<|>)
      | (?P<punct>[()\[\],.])
    """,
    re.VERBOSE,
)


class ExpressionReason(StrEnum):
    SYNTAX = "syntax"
    FUNCTION_CALL = "function_call"
    ATTRIBUTE_ACCESS = "attribute_access"
    SUBSCRIPT = "subscript"
    IMPORT = "import"
    FORBIDDEN_NAME = "forbidden_name"
    TOO_LONG = "too_long"
    TOO_DEEP = "too_deep"
    EVALUATION = "evaluation"


class ExpressionError(ValueError):
    """A `when` condition is unsafe, malformed or cannot be evaluated. The message never quotes it."""

    def __init__(self, reason: ExpressionReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason


# --- the tree --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Const:
    value: Literal


@dataclass(frozen=True, slots=True)
class _Name:
    name: str


@dataclass(frozen=True, slots=True)
class _List:
    items: tuple[Literal, ...]


@dataclass(frozen=True, slots=True)
class _Compare:
    op: str
    left: _Node
    right: _Node | _List


@dataclass(frozen=True, slots=True)
class _Not:
    operand: _Node


@dataclass(frozen=True, slots=True)
class _Bool:
    op: str
    operands: tuple[_Node, ...]


_Node = _Const | _Name | _Compare | _Not | _Bool


# --- tokens ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # str, num, name, op, punct, bad
    text: str
    at: int


def _tokens(source: str) -> list[_Token]:
    found: list[_Token] = []
    at = 0
    while at < len(source):
        match = _TOKEN.match(source, at)
        if match is None:
            found.append(_Token("bad", source[at], at))
            at += 1
            continue
        kind = match.lastgroup or "bad"
        if kind != "ws":
            found.append(_Token(kind, match.group(), at))
        at = match.end()
    return found


def _refuse_unsafe(tokens: list[_Token]) -> None:
    """The safety scan: the first unsafe construct, in source order, names the refusal."""
    for index, token in enumerate(tokens):
        before = tokens[index - 1] if index else None
        after = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.kind == "bad":
            raise ExpressionError(ExpressionReason.SYNTAX, f"unexpected character at {token.at}")
        if token.text == ".":
            if before is not None and before.kind == "num":
                raise ExpressionError(ExpressionReason.SYNTAX, f"only whole numbers are allowed (at {token.at})")
            raise ExpressionError(ExpressionReason.ATTRIBUTE_ACCESS, f"attribute access is not allowed (at {token.at})")
        if token.kind == "name":
            _refuse_name(token)
        callable_before = token.kind == "name" or token.text == ")" or token.kind == "str"
        if callable_before and after is not None and after.text == "(" and token.text not in _KEYWORDS:
            raise ExpressionError(ExpressionReason.FUNCTION_CALL, f"function calls are not allowed (at {after.at})")
        if callable_before and after is not None and after.text == "[" and token.text not in _KEYWORDS:
            raise ExpressionError(ExpressionReason.SUBSCRIPT, f"subscripts are not allowed (at {after.at})")


def _refuse_name(token: _Token) -> None:
    if token.text in _IMPORT_NAMES:
        raise ExpressionError(ExpressionReason.IMPORT, f"imports are not allowed (at {token.at})")
    if token.text in _FORBIDDEN_NAMES or token.text.startswith("__"):
        raise ExpressionError(ExpressionReason.FORBIDDEN_NAME, f"a reserved name is not allowed (at {token.at})")


# --- the parser ------------------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token], end: int) -> None:
        self._tokens = tokens
        self._end = end
        self._index = 0
        self._depth = 0

    def parse(self) -> _Node:
        if not self._tokens:
            raise ExpressionError(ExpressionReason.SYNTAX, "the condition is empty")
        node = self._or()
        if self._peek() is not None:
            raise self._syntax("unexpected input after the condition")
        return node

    def _peek(self, offset: int = 0) -> _Token | None:
        index = self._index + offset
        return self._tokens[index] if index < len(self._tokens) else None

    def _take(self) -> _Token:
        token = self._peek()
        if token is None:
            raise self._syntax("the condition ends too early")
        self._index += 1
        return token

    def _at(self, text: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token is not None and token.kind in {"name", "op", "punct"} and token.text == text

    def _syntax(self, detail: str) -> ExpressionError:
        token = self._peek()
        where = token.at if token is not None else self._end
        return ExpressionError(ExpressionReason.SYNTAX, f"{detail} (at {where})")

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise ExpressionError(ExpressionReason.TOO_DEEP, f"the condition nests deeper than {MAX_DEPTH}")

    def _or(self) -> _Node:
        operands = [self._and()]
        while self._at("or"):
            self._take()
            operands.append(self._and())
        return operands[0] if len(operands) == 1 else _Bool("or", tuple(operands))

    def _and(self) -> _Node:
        operands = [self._not()]
        while self._at("and"):
            self._take()
            operands.append(self._not())
        return operands[0] if len(operands) == 1 else _Bool("and", tuple(operands))

    def _not(self) -> _Node:
        if self._at("not") and not self._at("in", 1):
            self._take()
            self._enter()
            node = _Not(self._not())
            self._depth -= 1
            return node
        return self._compare()

    def _compare(self) -> _Node:
        left = self._operand()
        token = self._peek()
        if token is not None and token.kind == "op":
            self._take()
            node: _Node = _Compare(token.text, left, self._operand())
        elif self._at("in"):
            self._take()
            node = _Compare("in", left, self._list())
        elif self._at("not") and self._at("in", 1):
            self._take()
            self._take()
            node = _Compare("not in", left, self._list())
        elif self._at("not"):
            raise self._syntax("`not` here must be followed by `in`")
        else:
            return left
        following = self._peek()
        if following is not None and (following.kind == "op" or following.text in {"in", "not"}):
            raise self._syntax("comparisons cannot be chained")
        return node

    def _operand(self) -> _Node:
        token = self._take()
        if token.text == "(" and token.kind == "punct":
            self._enter()
            inner = self._or()
            if not self._at(")"):
                raise self._syntax("a `(` is not closed")
            self._take()
            self._depth -= 1
            return inner
        if token.kind == "name" and token.text not in _KEYWORDS:
            if token.text in _CONSTANTS:
                return _Const(_CONSTANTS[token.text])
            return _Name(token.text)
        if token.kind in {"str", "num"}:
            return _Const(_literal(token))
        raise ExpressionError(ExpressionReason.SYNTAX, f"expected a variable or a value (at {token.at})")

    def _list(self) -> _List:
        if not self._at("["):
            raise self._syntax("`in` takes a list of values, like ['a', 'b']")
        self._take()
        items: list[Literal] = []
        while not self._at("]"):
            token = self._take()
            if token.kind in {"str", "num"}:
                items.append(_literal(token))
            elif token.kind == "name" and token.text in _CONSTANTS:
                items.append(_CONSTANTS[token.text])
            else:
                raise ExpressionError(ExpressionReason.SYNTAX, f"a list holds values only (at {token.at})")
            if self._at(","):
                self._take()
            elif not self._at("]"):
                raise self._syntax("list items are separated by commas")
        self._take()
        return _List(tuple(items))


def _literal(token: _Token) -> Literal:
    return token.text[1:-1] if token.kind == "str" else int(token.text)


# --- evaluation ------------------------------------------------------------------------------------


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise ExpressionError(ExpressionReason.EVALUATION, f"a variable holds an unsupported {type(value).__name__}")


def _equal(left: Value, right: Value) -> bool:
    return _kind(left) == _kind(right) and left == right


def _order(op: str, left: Value, right: Value) -> bool:
    kinds = {_kind(left), _kind(right)}
    if len(kinds) != 1 or kinds - {"number", "string"}:
        raise ExpressionError(ExpressionReason.EVALUATION, f"`{op}` compares two numbers or two strings")
    return bool(_ORDER_OPS[op](left, right))


class _Evaluator:
    def __init__(self, variables: Mapping[str, object]) -> None:
        self._variables = variables

    def boolean(self, node: _Node) -> bool:
        value = self.value(node)
        if not isinstance(value, bool):
            raise ExpressionError(ExpressionReason.EVALUATION, "a condition must be true or false")
        return value

    def value(self, node: _Node) -> Value:
        if isinstance(node, _Const):
            return node.value
        if isinstance(node, _Name):
            if node.name not in self._variables:
                raise ExpressionError(ExpressionReason.EVALUATION, f"variable {node.name} has no value")
            value = self._variables[node.name]
            _kind(value)
            return cast(Value, value)
        if isinstance(node, _Not):
            return not self.boolean(node.operand)
        if isinstance(node, _Bool):
            if node.op == "and":
                return all(self.boolean(operand) for operand in node.operands)
            return any(self.boolean(operand) for operand in node.operands)
        return self._compare(node)

    def _compare(self, node: _Compare) -> bool:
        left = self.value(node.left)
        if isinstance(node.right, _List):
            found = any(_equal(left, item) for item in node.right.items)
            return found if node.op == "in" else not found
        right = self.value(node.right)
        if node.op == "==":
            return _equal(left, right)
        if node.op == "!=":
            return not _equal(left, right)
        return _order(node.op, left, right)


def _walk(node: _Node) -> Iterator[_Node]:
    yield node
    if isinstance(node, _Not):
        yield from _walk(node.operand)
    elif isinstance(node, _Bool):
        for operand in node.operands:
            yield from _walk(operand)
    elif isinstance(node, _Compare):
        yield from _walk(node.left)
        if not isinstance(node.right, _List):
            yield from _walk(node.right)


# --- the public object -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WhenExpression:
    """A parsed, safe `when` condition. Immutable; evaluate it with the hire's variables."""

    source: str
    _root: _Node

    @property
    def names(self) -> frozenset[str]:
        """The variables the condition reads."""
        return frozenset(node.name for node in _walk(self._root) if isinstance(node, _Name))

    def comparisons(self) -> Iterator[tuple[str, Literal]]:
        """Each `(variable, literal)` pair the condition compares, for checks like enum membership."""
        for node in _walk(self._root):
            if not isinstance(node, _Compare):
                continue
            pairs = ((node.left, node.right), (node.right, node.left))
            for name, other in pairs:
                if not isinstance(name, _Name):
                    continue
                if isinstance(other, _Const):
                    yield name.name, other.value
                elif isinstance(other, _List):
                    yield from ((name.name, item) for item in other.items)

    def evaluate(self, variables: Mapping[str, object]) -> bool:
        """True or false for these variables; `ExpressionError(EVALUATION)` when it cannot be decided."""
        return _Evaluator(variables).boolean(self._root)


def parse_when(source: str) -> WhenExpression:
    """Parse a `when` condition, or raise `ExpressionError` naming why it is refused."""
    if not isinstance(source, str):
        raise ExpressionError(ExpressionReason.SYNTAX, "a condition is text")
    if len(source) > MAX_LENGTH:
        raise ExpressionError(ExpressionReason.TOO_LONG, f"a condition is at most {MAX_LENGTH} characters")
    tokens = _tokens(source)
    _refuse_unsafe(tokens)
    return WhenExpression(source, _Parser(tokens, len(source)).parse())
