"""Property tests for the query validator (MCP-2): an accepted query never contains a forbidden
operator, however the generator nests, spells or places it."""

from __future__ import annotations

import unicodedata
from typing import Any

from hypothesis import HealthCheck, assume, event, given, settings
from hypothesis import strategies as st
from onb_mcp_mongo.policy import ALLOWED_STAGES, ToolRejectedError
from onb_mcp_mongo.validator import (
    ACCUMULATOR_OPERATORS,
    EXPRESSION_OPERATORS,
    FORBIDDEN_OPERATORS,
    QUERY_OPERATORS,
    UPDATE_OPERATORS,
    check_call,
)

from .corpus import R, W

# Every `$` name the validator may let through somewhere, plus the ObjectId and `$push` forms.
ALLOWED_NAMES = frozenset(
    QUERY_OPERATORS | EXPRESSION_OPERATORS | ACCUMULATOR_OPERATORS | UPDATE_OPERATORS | ALLOWED_STAGES
) | {"$oid", "$each", "$type"}

SETTINGS = settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Keys: allowed operators, forbidden ones, look-alikes and plain field names, so trees often pass
# and often carry something dangerous.
_lookalikes = [
    "\uff04where",
    "\ufe69function",
    "$wh\u0435re",
    "$\u200bwhere",
    "$WHERE",
    "$where ",
    " $where",
    "$lookup\x00",
    "$func\u00adtion",
]
KEYS = st.one_of(
    st.sampled_from(sorted(ALLOWED_NAMES)),
    st.sampled_from(sorted(FORBIDDEN_OPERATORS)),
    st.sampled_from(_lookalikes),
    st.sampled_from(["status", "a", "a.b", "pii.mobile", "org_id", "tags", "items.$", "x.$where", "$"]),
    st.text(min_size=1, max_size=8),
    st.text(min_size=1, max_size=6).map(lambda s: "$" + s),
)
LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-5, 5),
    st.sampled_from(["$a", "$$NOW", "open", "org_id", "$where", "function () { return 1; }"]),
    st.text(max_size=6),
)
TREES = st.recursive(
    LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(KEYS, children, max_size=3),
    ),
    max_leaves=25,
)


def _names(node: Any) -> list[str]:
    """Every key at any depth, and every segment of a dotted key."""
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.append(key)
            out.extend(key.split("."))
            out.extend(_names(value))
        return out
    if isinstance(node, list | tuple):
        return [name for item in node for name in _names(item)]
    return []


def _dangerous(name: str) -> bool:
    canonical = unicodedata.normalize("NFKC", name)
    if canonical in FORBIDDEN_OPERATORS or name in FORBIDDEN_OPERATORS:
        return True
    starts_like_dollar = canonical.lstrip().startswith("$") or name.startswith("$")
    positional = name in {"$", "$[]"}
    return starts_like_dollar and not positional and name not in ALLOWED_NAMES


def _accepted(tool: str, args: dict[str, Any]) -> bool:
    try:
        check_call(tool, args)
    except ToolRejectedError:
        return False
    return True


def _assert_safe_if_accepted(tool: str, args: dict[str, Any]) -> None:
    accepted = _accepted(tool, args)
    event(f"{tool}: {'accepted' if accepted else 'rejected'}")
    if accepted:
        bad = [name for name in _names(args) if _dangerous(name)]
        assert not bad, (tool, bad)
        if tool == "aggregate":
            assert all(next(iter(stage)) in ALLOWED_STAGES for stage in args["pipeline"])
        _assert_context_names(tool, args)


def _operator_names(node: Any) -> set[str]:
    return {name for name in _names(node) if name.startswith("$") and name not in {"$", "$[]"}}


def _assert_context_names(tool: str, args: dict[str, Any]) -> None:
    """Each argument holds only the operators of its own context, not just any allowlisted one."""
    if "filter" in args:
        # Filters: query operators, expressions under $expr, and the ObjectId form; never an update
        # operator, a stage or an accumulator-only name.
        assert _operator_names(args["filter"]) <= QUERY_OPERATORS | EXPRESSION_OPERATORS | {"$oid"}
    if tool == "insert_one":
        assert _operator_names(args["document"]) <= {"$oid"}
    if tool == "update_one":
        assert set(args["update"]) <= UPDATE_OPERATORS
        for fields in args["update"].values():
            assert not {path for path in fields if path.split(".")[0] in {"$", "$[]"}}


@SETTINGS
@given(TREES)
def test_an_accepted_filter_never_holds_a_forbidden_operator(tree: Any) -> None:
    assume(isinstance(tree, dict))
    _assert_safe_if_accepted("find", {"collection": R, "filter": tree, "limit": 10})
    _assert_safe_if_accepted("count", {"collection": R, "filter": tree})


@SETTINGS
@given(st.lists(st.dictionaries(KEYS, TREES, min_size=1, max_size=1), min_size=1, max_size=4))
def test_an_accepted_pipeline_never_holds_a_forbidden_operator(pipeline: list[dict[str, Any]]) -> None:
    _assert_safe_if_accepted("aggregate", {"collection": R, "pipeline": pipeline})


@SETTINGS
@given(TREES, st.dictionaries(KEYS, TREES, min_size=1, max_size=3))
def test_an_accepted_update_never_holds_a_forbidden_operator(filter_: Any, update: dict[str, Any]) -> None:
    assume(isinstance(filter_, dict) and filter_)
    args = {"collection": W, "filter": filter_, "update": update}
    _assert_safe_if_accepted("update_one", args)
    if _accepted("update_one", args):
        paths = [path for fields in update.values() if isinstance(fields, dict) for path in fields]
        targets = [t for t in update.get("$rename", {}).values() if isinstance(t, str)]
        for path in paths + targets:
            assert path.split(".")[0] not in {"pii", "org_id"}, path


@SETTINGS
@given(st.dictionaries(KEYS, TREES, max_size=4))
def test_an_accepted_insert_never_holds_a_forbidden_operator_pii_or_org(document: dict[str, Any]) -> None:
    args = {"collection": W, "document": document}
    _assert_safe_if_accepted("insert_one", args)
    if _accepted("insert_one", args):
        assert all(key.split(".")[0] not in {"pii", "org_id"} for key in document)


# --- grammar-built queries: accepted as they are, rejected once anything forbidden is put in ------

FIELDS = st.sampled_from(["status", "team", "profile.name", "tasks", "start"])
SCALARS = st.one_of(st.integers(-3, 3), st.sampled_from(["open", "done", "eng"]), st.booleans())
COMPARISON = st.sampled_from(["$eq", "$ne", "$gt", "$gte", "$lt", "$lte"])


def _field_condition() -> st.SearchStrategy[Any]:
    return st.one_of(
        SCALARS,
        st.dictionaries(COMPARISON, SCALARS, min_size=1, max_size=2),
        st.builds(lambda xs: {"$in": xs}, st.lists(SCALARS, max_size=3)),
        st.builds(lambda v: {"$exists": v}, st.booleans()),
    )


VALID_FILTERS = st.recursive(
    st.dictionaries(FIELDS, _field_condition(), min_size=1, max_size=3),
    lambda inner: st.one_of(
        st.builds(lambda xs: {"$and": xs}, st.lists(inner, min_size=1, max_size=3)),
        st.builds(lambda xs: {"$or": xs}, st.lists(inner, min_size=1, max_size=3)),
        st.builds(lambda f, c: {f: {"$elemMatch": c}}, FIELDS, inner),
    ),
    max_leaves=8,
)


def _inject(node: Any, operator: str, path: list[int]) -> Any:
    """A copy of `node` with `{operator: ...}` added inside the dict that `path` leads to."""
    if isinstance(node, dict):
        keys = list(node)
        if not path or not keys:
            return {**node, operator: {"body": "function () {}"}}
        key = keys[path[0] % len(keys)]
        return {**node, key: _inject(node[key], operator, path[1:])}
    if isinstance(node, list) and node:
        index = path[0] % len(node) if path else 0
        return [*node[:index], _inject(node[index], operator, path[1:]), *node[index + 1 :]]
    return {operator: {"body": "function () {}"}}


@SETTINGS
@given(VALID_FILTERS, st.sampled_from(sorted(FORBIDDEN_OPERATORS)), st.lists(st.integers(0, 5), max_size=8))
def test_a_valid_filter_is_accepted_and_rejected_once_a_forbidden_operator_is_injected(
    filter_: dict[str, Any], operator: str, path: list[int]
) -> None:
    assert _accepted("find", {"collection": R, "filter": filter_, "limit": 10})
    poisoned = _inject(filter_, operator, path)
    assert not _accepted("find", {"collection": R, "filter": poisoned, "limit": 10})
    assert not _accepted("aggregate", {"collection": R, "pipeline": [{"$match": poisoned}]})


# --- grammar-built calls with mutated keys: mostly well-formed, so many are accepted, and any key
# --- position may carry a forbidden operator, a look-alike, an unknown `$name`, pii or org_id ------

DANGER = st.one_of(
    st.sampled_from(sorted(FORBIDDEN_OPERATORS)),
    st.sampled_from(_lookalikes),
    st.sampled_from(["pii", "pii.mobile", "org_id", "org_id.x", "a.$where", "a..b", "$regex", "$text", "$code"]),
    st.text(min_size=1, max_size=5).map(lambda s: "$" + s),
)


def _key(valid: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Usually a valid key for the position, about one in twenty times a dangerous one."""
    return st.integers(0, 19).flatmap(lambda n: DANGER if n == 0 else valid)


PATHS = st.sampled_from(["status", "team", "profile.name", "tasks", "items.$", "all.$[].seen"])
FIELD_KEYS = _key(FIELDS)
EXPRESSIONS = st.recursive(
    st.one_of(SCALARS, st.sampled_from(["$status", "$tasks", "$$NOW"])),
    lambda inner: st.dictionaries(
        _key(st.sampled_from(["$add", "$eq", "$gt", "$size", "$concat", "$cond", "$toLower"])),
        st.lists(inner, max_size=2),
        min_size=1,
        max_size=1,
    ),
    max_leaves=6,
)
CONDITIONS = st.one_of(
    SCALARS,
    st.dictionaries(_key(COMPARISON), SCALARS, min_size=1, max_size=2),
    st.builds(lambda c: {"$not": c}, st.dictionaries(_key(COMPARISON), SCALARS, min_size=1, max_size=1)),
)
LOGICAL = _key(st.sampled_from(["$and", "$or", "$nor"]))
QUERIES = st.recursive(
    st.one_of(
        st.dictionaries(FIELD_KEYS, CONDITIONS, min_size=1, max_size=3),
        st.builds(lambda e: {"$expr": e}, EXPRESSIONS),
    ),
    lambda inner: st.one_of(
        st.dictionaries(LOGICAL, st.lists(inner, min_size=1, max_size=2), min_size=1, max_size=1),
        st.builds(lambda f, c: {f: {"$elemMatch": c}}, FIELD_KEYS, inner),
    ),
    max_leaves=6,
)
LITERALS = st.recursive(
    SCALARS,
    lambda inner: st.one_of(st.lists(inner, max_size=2), st.dictionaries(FIELD_KEYS, inner, max_size=2)),
    max_leaves=6,
)
ACCUMULATORS = st.dictionaries(
    _key(st.sampled_from(["$sum", "$max", "$push", "$first"])), EXPRESSIONS, min_size=1, max_size=1
)


def _stage(name: str, body: st.SearchStrategy[Any]) -> st.SearchStrategy[dict[str, Any]]:
    return st.builds(lambda key, value: {key: value}, _key(st.just(name)), body)


STAGES = st.one_of(
    _stage("$match", QUERIES),
    _stage("$project", st.dictionaries(FIELD_KEYS, st.one_of(st.just(1), EXPRESSIONS), min_size=1, max_size=2)),
    _stage("$addFields", st.dictionaries(FIELD_KEYS, EXPRESSIONS, min_size=1, max_size=2)),
    _stage(
        "$group",
        st.builds(
            lambda gid, out: {"_id": gid, **out}, EXPRESSIONS, st.dictionaries(FIELD_KEYS, ACCUMULATORS, max_size=2)
        ),
    ),
    _stage("$sort", st.dictionaries(FIELD_KEYS, st.sampled_from([1, -1]), min_size=1, max_size=2)),
    _stage("$limit", st.integers(1, 50)),
    _stage("$count", FIELD_KEYS),
    _stage("$unwind", FIELD_KEYS.map(lambda f: "$" + f)),
)
UPDATES = st.dictionaries(
    _key(st.sampled_from(["$set", "$unset", "$inc", "$push", "$min"])),
    st.dictionaries(_key(PATHS), LITERALS, min_size=1, max_size=2),
    min_size=1,
    max_size=2,
)
DOCUMENTS = st.dictionaries(FIELD_KEYS, LITERALS, max_size=3)


@SETTINGS
@given(QUERIES)
def test_a_mutated_filter_is_accepted_only_without_a_forbidden_operator(filter_: dict[str, Any]) -> None:
    _assert_safe_if_accepted("find", {"collection": R, "filter": filter_, "limit": 10})


@SETTINGS
@given(st.lists(STAGES, min_size=1, max_size=4))
def test_a_mutated_pipeline_is_accepted_only_without_a_forbidden_operator(pipeline: list[dict[str, Any]]) -> None:
    _assert_safe_if_accepted("aggregate", {"collection": R, "pipeline": pipeline})


@SETTINGS
@given(QUERIES, UPDATES)
def test_a_mutated_update_is_accepted_only_without_a_forbidden_operator_pii_or_org(
    filter_: dict[str, Any], update: dict[str, Any]
) -> None:
    args = {"collection": W, "filter": filter_, "update": update}
    _assert_safe_if_accepted("update_one", args)
    if _accepted("update_one", args):
        for fields in update.values():
            for path in fields:
                assert path.split(".")[0] not in {"pii", "org_id"}, path


@SETTINGS
@given(DOCUMENTS)
def test_a_mutated_insert_is_accepted_only_without_a_forbidden_operator_pii_or_org(document: dict[str, Any]) -> None:
    args = {"collection": W, "document": document}
    _assert_safe_if_accepted("insert_one", args)
    if _accepted("insert_one", args):
        assert all(key.split(".")[0] not in {"pii", "org_id"} for key in document)
