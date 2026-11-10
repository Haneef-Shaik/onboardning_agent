"""C-1 mutation checks: break each guard on purpose, run its tests, expect a named failure, restore.

    uv run --frozen python docs/evidence/C-1/mutate.py > docs/evidence/C-1/mutation-checks.txt

M1 is also box 1's "seen to fail once": a real route declares `org_id` and the walker test fails.
Every file is restored byte for byte; the script exits non-zero if a mutation survives.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = "apps/api/src/onb_api"
CONTRACTS = "libs/py-contracts/src/onb_contracts"
WALK = "apps/api/tests/test_routes.py::test_no_request_model_or_parameter_declares_org_id"
UNHANDLED = "apps/api/tests/test_errors.py::test_unhandled_exception_returns_the_envelope_with_a_stable_code_and_no_stack_trace"


@dataclass(frozen=True)
class Mutation:
    key: str
    what: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]
    expect_failed: tuple[str, ...]


MUTATIONS = (
    Mutation(
        "M1",
        "box 1 seen to fail: GET /health declares an `org_id` query parameter",
        f"{API}/health.py",
        "async def health() -> Envelope[Health]:",
        "async def health(org_id: str | None = None) -> Envelope[Health]:",
        (WALK,),
        ("test_no_request_model_or_parameter_declares_org_id",),
    ),
    Mutation(
        "M2",
        "box 1: a RequestModel subclass declares `org_id`",
        f"{CONTRACTS}/health.py",
        'class HealthEnvelope(Envelope[Health]):\n    """The API is up."""\n',
        'class HealthEnvelope(Envelope[Health]):\n    """The API is up."""\n\n\n'
        "from onb_contracts.inputs import RequestModel  # noqa: E402\n\n\n"
        "class Probe(RequestModel):\n    org_id: str\n",
        (WALK,),
        ("test_no_request_model_or_parameter_declares_org_id",),
    ),
    Mutation(
        "M3",
        "box 1: the tenant-key matcher never matches",
        f"{CONTRACTS}/inputs.py",
        "    if len(words) == 1 and (words[0] in _TENANT_WORDS or words[0] in _TENANT_IDS):\n"
        "        return True\n"
        '    return any(f"{a}{b}" in _TENANT_IDS for a, b in itertools.pairwise(words))',
        "    return False",
        ("libs/py-contracts/tests/test_request_inputs.py", "apps/api/tests/test_routes.py"),
        (
            "test_tenant_keys_are_recognised_in_any_spelling",
            "test_a_model_declaring_org_id_is_found",
            "test_a_parameter_named_org_id_is_found",
            "test_the_route_walk_finds_org_id_however_it_is_declared",
        ),
    ),
    Mutation(
        "M4",
        "box 4: the unhandled-error middleware is not installed",
        f"{API}/errors.py",
        "    app.add_middleware(UnhandledErrorMiddleware)",
        "    pass",
        ("apps/api/tests/test_errors.py",),
        ("test_unhandled_exception_returns_the_envelope_with_a_stable_code_and_no_stack_trace",),
    ),
    Mutation(
        "M5",
        "box 4: the 500 answer carries the exception text",
        f"{API}/errors.py",
        "            await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send)",
        "            await error_response(ErrorCode.INTERNAL_ERROR, repr(exc))(scope, receive, send)",
        (UNHANDLED,),
        ("test_unhandled_exception_returns_the_envelope_with_a_stable_code_and_no_stack_trace",),
    ),
    Mutation(
        "M6",
        "box 4: the server log carries the traceback (and so the message)",
        f"{API}/errors.py",
        "kind, scope.get(\"method\", \"?\"), path, location)",
        "kind, scope.get(\"method\", \"?\"), path, location, exc_info=exc)",
        ("apps/api/tests/test_errors.py",),
        ("test_unhandled_exception_log_names_the_type_but_not_the_message",),
    ),
    Mutation(
        "M7",
        "validation details pass Pydantic's `msg` through (it can carry the value)",
        f"{API}/validation.py",
        '            issue=issue_for(str(err.get("type", ""))),',
        '            issue=str(err.get("msg", "")),',
        ("apps/api/tests/test_errors.py",),
        ("test_a_validator_message_carrying_the_value_is_not_echoed", "test_issue_texts_are_fixed_per_error_type"),
    ),
    Mutation(
        "M8",
        "an HTTPException's detail reaches the client",
        f"{API}/errors.py",
        "return error_response(code_for_status(exc.status_code), status=exc.status_code, headers=headers)",
        "return error_response(code_for_status(exc.status_code), str(exc.detail), status=exc.status_code, headers=headers)",
        ("apps/api/tests/test_errors.py",),
        ("test_http_exception_uses_the_code_for_its_status_and_not_its_detail",),
    ),
    Mutation(
        "M9",
        "the envelope accepts a failure without an error",
        f"{CONTRACTS}/envelope.py",
        '            raise ValueError("a failure envelope must carry an error")',
        "            pass",
        ("libs/py-contracts/tests/test_envelope.py",),
        ("test_failure_without_an_error_is_rejected",),
    ),
    Mutation(
        "M10",
        "box 2: the drift check compares nothing",
        "scripts/contracts.py",
        "    return [(path, want) for path, want in pairs if _read(path) != want]",
        "    return []",
        ("tools/tests/test_contracts.py",),
        ("test_a_stale_openapi_document_is_drift", "test_a_stale_ts_client_is_drift", "test_check_fails_with_a_diff_and_the_fix"),
    ),
    Mutation(
        "M11",
        "telemetry: create_app never instruments the app",
        f"{API}/app.py",
        "    if telemetry_is_active():",
        "    if False:",
        ("apps/api/tests/test_telemetry.py",),
        ("test_requests_get_server_spans_and_meta_carries_the_trace_id",),
    ),
    Mutation(
        "M12",
        "telemetry: an unhandled error is not recorded on the span",
        f"{API}/errors.py",
        '    span.set_attribute("error.type", kind)\n',
        "",
        ("apps/api/tests/test_telemetry.py",),
        ("test_an_unhandled_error_marks_the_server_span_as_failed_without_the_message",),
    ),
    Mutation(
        "M13",
        "validation locations keep a client-chosen dict key",
        f"{API}/validation.py",
        "        elif node is not None and isinstance(node.get(\"additionalProperties\"), Mapping):\n"
        "            parts.append(WILDCARD)\n",
        "        elif node is not None and isinstance(node.get(\"additionalProperties\"), Mapping):\n"
        "            parts.append(str(segment))\n",
        ("apps/api/tests/test_errors.py", "apps/api/tests/test_validation_details.py"),
        ("test_user_chosen_keys_never_appear_in_field_locations",),
    ),
    Mutation(
        "M14",
        "a failure after the response started is re-raised as is (message reaches OTel and logs)",
        f"{API}/errors.py",
        '                raise RuntimeError(f"unhandled {type(exc).__name__} after the response started") from None',
        "                raise",
        ("apps/api/tests/test_errors.py",),
        ("test_a_failure_after_the_response_started_is_re_raised_without_its_message",),
    ),
    Mutation(
        "M15",
        "box 1: the model walk checks wire names only (an alias hides `org_id`)",
        f"{CONTRACTS}/inputs.py",
        "    for by_alias in (True, False):",
        "    for by_alias in (True,):",
        ("libs/py-contracts/tests/test_request_inputs.py",),
        ("test_an_alias_hiding_the_python_name_org_id_is_found",),
    ),
)


def run_mutation(m: Mutation) -> bool:
    path = ROOT / m.file
    original = path.read_bytes()
    text = original.decode("utf-8")
    if text.count(m.old) != 1:
        print(f"{m.key}: SKIPPED, target text found {text.count(m.old)} times in {m.file}")
        return False
    print(f"## {m.key}: {m.what}\n   {m.file}: {m.old.strip()!r} -> {m.new.strip()!r}")
    try:
        path.write_text(text.replace(m.old, m.new), encoding="utf-8")
        cmd = ["uv", "run", "--frozen", "pytest", *m.tests, "-q", "-p", "no:cacheprovider", "-W", "ignore"]
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    finally:
        path.write_bytes(original)
    failed_lines = [ln for ln in done.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    for line in failed_lines:
        print(f"   {line[:200]}")
    print(f"   {done.stdout.strip().splitlines()[-1] if done.stdout.strip() else '(no output)'}")
    caught = done.returncode != 0 and all(any(name in ln for ln in failed_lines) for name in m.expect_failed)
    restored = path.read_bytes() == original
    print(f"   caught={caught} restored={restored}\n")
    return caught and restored


def main() -> int:
    results = {m.key: run_mutation(m) for m in MUTATIONS}
    caught = [k for k, v in results.items() if v]
    print(f"SUMMARY: {len(caught)} of {len(results)} mutations caught; survivors: {[k for k, v in results.items() if not v]}")
    return 0 if len(caught) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
