"""The `when` condition language (PRD §5.1, §6.1; TPL-1 box 3).

A `when` is parsed by a small recursive-descent parser into a closed AST and evaluated by walking it.
Nothing reaches `eval`, `exec`, `compile` or Python's `ast`: function calls, attribute access,
subscripts, imports and dunder names are refused by the parser with a named reason.
"""

from __future__ import annotations

import pytest
from onb_agents.templates.expr import (
    MAX_DEPTH,
    MAX_LENGTH,
    ExpressionError,
    ExpressionReason,
    WhenExpression,
    parse_when,
)

# --- box 3: function calls, attribute access and imports are rejected ----------------------------


@pytest.mark.parametrize(
    "source",
    [
        "len(role) > 3",
        "role == str(1)",
        "open('/etc/passwd')",
        "role == 'engineer' and exit()",
        "(lambda: 1)()",
        "role ()",
    ],
)
def test_the_when_parser_rejects_function_calls(source: str) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason in {ExpressionReason.FUNCTION_CALL, ExpressionReason.FORBIDDEN_NAME}


@pytest.mark.parametrize(
    "source",
    [
        "role.upper() == 'ENGINEER'",
        "hire.role == 'engineer'",
        "role.__class__ == 'x'",
        "'engineer'.startswith('e')",
        "role . lower",
    ],
)
def test_the_when_parser_rejects_attribute_access(source: str) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason == ExpressionReason.ATTRIBUTE_ACCESS


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "__import__('os').system('id')",
        "__import__",
        "importlib == 'x'",
        "from os import system",
        "role == 'x' and import",
    ],
)
def test_the_when_parser_rejects_imports(source: str) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason == ExpressionReason.IMPORT


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("role[0] == 'e'", ExpressionReason.SUBSCRIPT),
        ("__builtins__ == 1", ExpressionReason.FORBIDDEN_NAME),
        ("eval == 1", ExpressionReason.FORBIDDEN_NAME),
        ("lambda == 1", ExpressionReason.FORBIDDEN_NAME),
        ("role == 'a' if x else 'b'", ExpressionReason.SYNTAX),
        ("role = 'engineer'", ExpressionReason.SYNTAX),
        ("role == 'engineer", ExpressionReason.SYNTAX),
        ("role == 'a\\'b'", ExpressionReason.SYNTAX),
        ("role == 'engineer' ;", ExpressionReason.SYNTAX),
        ("role == 'engineer' role", ExpressionReason.SYNTAX),
        ("", ExpressionReason.SYNTAX),
        ("   ", ExpressionReason.SYNTAX),
        ("1 < x < 3", ExpressionReason.SYNTAX),
        ("[role] == 1", ExpressionReason.SYNTAX),
        ("x in [role]", ExpressionReason.SYNTAX),
        ("x in [1,,2]", ExpressionReason.SYNTAX),
        ("x == 1.5e9", ExpressionReason.SYNTAX),
        ("x == -", ExpressionReason.SYNTAX),
        ("not", ExpressionReason.SYNTAX),
        ("x == 'a' and", ExpressionReason.SYNTAX),
        ("(x == 1", ExpressionReason.SYNTAX),
        ("x == 1)", ExpressionReason.SYNTAX),
        ("x == {1}", ExpressionReason.SYNTAX),
        ("x == `id`", ExpressionReason.SYNTAX),
        ("x not == 1", ExpressionReason.SYNTAX),
        ("x == 1 * 2", ExpressionReason.SYNTAX),
        ("x in []]", ExpressionReason.SYNTAX),
        ("role in 'engineer'", ExpressionReason.SYNTAX),
        ("x == \u0661\u0662", ExpressionReason.SYNTAX),
        ("x ==\u00a01", ExpressionReason.SYNTAX),
        ("x\u2003== 1", ExpressionReason.SYNTAX),
        ("x in role", ExpressionReason.SYNTAX),
        ("x == 1.5", ExpressionReason.SYNTAX),
    ],
)
def test_other_unsafe_or_malformed_conditions_are_rejected(source: str, reason: ExpressionReason) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason == reason


def test_a_condition_longer_than_the_limit_is_rejected() -> None:
    source = "role == '" + "a" * MAX_LENGTH + "'"
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason == ExpressionReason.TOO_LONG


def test_a_condition_nested_deeper_than_the_limit_is_rejected() -> None:
    source = "(" * (MAX_DEPTH + 1) + "x == 1" + ")" * (MAX_DEPTH + 1)
    with pytest.raises(ExpressionError) as caught:
        parse_when(source)
    assert caught.value.reason == ExpressionReason.TOO_DEEP


def test_a_long_chain_of_nots_is_bounded_too() -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when("not " * (MAX_DEPTH + 1) + "flag")
    assert caught.value.reason == ExpressionReason.TOO_DEEP


def test_the_error_message_never_echoes_the_condition() -> None:
    synthetic_pan = "ABCDE1234F"
    with pytest.raises(ExpressionError) as caught:
        parse_when(f"pan.value == '{synthetic_pan}'")
    assert synthetic_pan not in str(caught.value)


def test_a_non_string_condition_is_rejected() -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(42)  # type: ignore[arg-type]
    assert caught.value.reason == ExpressionReason.SYNTAX


# --- the language that is accepted ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "variables", "expected"),
    [
        ("role == 'engineer'", {"role": "engineer"}, True),
        ("role == 'engineer'", {"role": "sales"}, False),
        ('role != "sales"', {"role": "engineer"}, True),
        ("role in ['engineer', 'designer']", {"role": "designer"}, True),
        ("role in ['engineer', 'designer',]", {"role": "sales"}, False),
        ("role not in ['sales']", {"role": "engineer"}, True),
        ("role in []", {"role": "engineer"}, False),
        ("level >= 3", {"level": 3}, True),
        ("level > 3", {"level": 3}, False),
        ("level < -1", {"level": -2}, True),
        ("level <= 0", {"level": 1}, False),
        ("start_date < '2026-12-01'", {"start_date": "2026-11-24"}, True),
        ("remote == true", {"remote": True}, True),
        ("remote == True", {"remote": False}, False),
        ("remote", {"remote": True}, True),
        ("not remote", {"remote": True}, False),
        ("not not remote", {"remote": True}, True),
        ("manager_id != null", {"manager_id": None}, False),
        ("manager_id == None", {"manager_id": None}, True),
        ("role == 'engineer' and location == 'Bangalore'", {"role": "engineer", "location": "Pune"}, False),
        ("role == 'sales' or location == 'Pune'", {"role": "engineer", "location": "Pune"}, True),
        ("not (role == 'sales' or role == 'designer')", {"role": "engineer"}, True),
        ("a == 1 or b == 1 and c == 1", {"a": 1, "b": 0, "c": 0}, True),
        ("(a == 1 or b == 1) and c == 1", {"a": 1, "b": 0, "c": 0}, False),
        ("flag == false", {"flag": False}, True),
        ("n == 1", {"n": True}, False),
        ("n != 1", {"n": True}, True),
        ("n == 0", {"n": False}, False),
        ("n in [1]", {"n": True}, False),
        ("x\t==\n'a'", {"x": "a"}, True),
        ("ROLE == 'x'", {"ROLE": "x"}, True),
        ("_private == 1", {"_private": 1}, True),
        ("1 == 1", {}, True),
        ("true", {}, True),
    ],
)
def test_accepted_conditions_evaluate(source: str, variables: dict[str, object], expected: bool) -> None:
    assert parse_when(source).evaluate(variables) is expected


def test_the_names_a_condition_reads_are_listed() -> None:
    parsed = parse_when("role == 'engineer' and (location in ['Pune'] or not remote) and 1 == 1")
    assert parsed.names == frozenset({"role", "location", "remote"})


def test_the_variable_pairs_compared_are_listed() -> None:
    parsed = parse_when("role == location or level > 1 or not (a != b)")
    assert sorted(parsed.name_pairs()) == [("a", "b"), ("role", "location")]


def test_the_literals_compared_with_each_name_are_listed() -> None:
    parsed = parse_when("role == 'engineer' or 'sales' == role or role in ['designer', 'x'] or level > 2")
    assert sorted(parsed.comparisons()) == [
        ("level", 2),
        ("role", "designer"),
        ("role", "engineer"),
        ("role", "sales"),
        ("role", "x"),
    ]


def test_a_parsed_condition_is_immutable_and_keeps_its_source() -> None:
    parsed = parse_when("role == 'engineer'")
    assert isinstance(parsed, WhenExpression)
    assert parsed.source == "role == 'engineer'"
    with pytest.raises(AttributeError):
        parsed.source = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("source", "variables"),
    [
        ("role == 'engineer'", {}),
        ("level > 'three'", {"level": 3}),
        ("level > 3", {"level": True}),
        ("level > 3", {"level": None}),
        ("role", {"role": "engineer"}),
        ("role and true", {"role": "engineer"}),
        ("not role", {"role": "engineer"}),
        ("role == 'x'", {"role": object()}),
        ("role == 'x'", {"role": ["x"]}),
    ],
)
def test_evaluation_fails_closed_on_missing_or_mistyped_values(source: str, variables: dict[str, object]) -> None:
    with pytest.raises(ExpressionError) as caught:
        parse_when(source).evaluate(variables)
    assert caught.value.reason == ExpressionReason.EVALUATION


def test_the_module_never_uses_eval_exec_compile_or_python_ast() -> None:
    import ast
    import inspect

    from onb_agents.templates import expr

    tree = ast.parse(inspect.getsource(expr))
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "getattr", "literal_eval"}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"ast", "builtins", "importlib"}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "literal_eval" not in attributes
