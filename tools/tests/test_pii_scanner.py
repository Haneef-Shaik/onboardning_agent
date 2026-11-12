"""SEC-1: the leak scanner behind `make test-pii` finds a planted value in every kind of source it scans,
in the forms people write it, and excuses nothing but opaque ids and named allow rules."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tools" / "tests_pii"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"pii_{name}", SUITE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan_mod = _load("scan")
fixture_mod = _load("fixture")

FIXTURE: Mapping[str, str] = fixture_mod.PiiFixture.generate().values()
F = dict(FIXTURE)


def _scan(*items: Any, allow: tuple[Any, ...] = ()) -> Any:
    return scan_mod.scan(items, FIXTURE, allow)


def _item(text: str, source: str = "mongo", location: str = "c[0].f") -> Any:
    return scan_mod.Item(source, location, text)


def _raw_labels(result: Any) -> set[str]:
    return {h.label for h in result.hits if h.kind == "raw"}


def test_fixture_values_are_fresh_and_valid() -> None:
    other = fixture_mod.PiiFixture.generate().values()
    assert set(other) == set(FIXTURE)
    assert all(other[k] != FIXTURE[k] for k in FIXTURE)


def test_clean_text_has_no_hits() -> None:
    result = _scan(_item("Welcome, Priya! You start on 2026-12-01. recipient=ptk_01JD5Z8S3XWQY6R7T9V0B1C2D3E4F5G6"))
    assert result.clean
    assert result.report()["result"] == "clean"


@pytest.mark.parametrize("label", sorted(FIXTURE))
def test_every_fixture_value_is_found_verbatim(label: str) -> None:
    result = _scan(_item(f"prefix {FIXTURE[label]} suffix"))
    assert label in _raw_labels(result)


def test_numbers_are_found_in_the_forms_people_write_them() -> None:
    mobile, aadhaar, account = F["mobile"], F["aadhaar"], F["bank_account"]
    forms = [
        f"+91 {mobile[:5]} {mobile[5:]}",
        f"0{mobile}",
        f"{mobile[:3]}-{mobile[3:6]}-{mobile[6:]}",
        f"{aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}",
        f"{aadhaar[:4]}.{aadhaar[4:8]}.{aadhaar[8:]}",
        " ".join(account[i : i + 4] for i in range(0, 12, 4)),
    ]
    labels = [_raw_labels(_scan(_item(form))) for form in forms]
    assert labels == [{"mobile"}, {"mobile"}, {"mobile"}, {"aadhaar"}, {"aadhaar"}, {"bank_account"}]


def test_codes_and_names_are_found_in_any_case_and_split() -> None:
    pan, ifsc, email, name = F["pan"], F["ifsc"], F["email"], F["account_holder_name"]
    assert "pan" in _raw_labels(_scan(_item(pan.lower())))
    assert "pan" in _raw_labels(_scan(_item(f"{pan[:5]} {pan[5:9]} {pan[9]}")))
    assert "ifsc" in _raw_labels(_scan(_item(ifsc.lower())))
    assert "email" in _raw_labels(_scan(_item(email.upper())))
    assert "email" in _raw_labels(_scan(_item(email.replace("@", "%40"))))
    assert "account_holder_name" in _raw_labels(_scan(_item(name.upper())))
    assert "account_holder_name" in _raw_labels(_scan(_item(name.replace(" ", "%20"))))


def test_a_value_inside_a_longer_number_is_not_that_value() -> None:
    assert "mobile" not in _raw_labels(_scan(_item("1" + F["mobile"] + "2")))


def test_a_planted_value_is_found_in_every_kind_of_source() -> None:
    """Box 2's unit-level control: a document, a log line, a JSON log record, a span export, an outbox
    message, an event payload, an LLM input, binary content and a document key all give a raw hit."""
    mobile, pan, email = F["mobile"], F["pan"], F["email"]
    sources: dict[str, list[Any]] = {
        "mongo": list(scan_mod.flatten("mongo", "new_hires[0]", {"notes": f"call {mobile}"})),
        "mongo-key": list(scan_mod.flatten("mongo", "new_hires[0]", {"by_mobile": {mobile: 1}})),
        "mongo-int": list(scan_mod.flatten("mongo", "new_hires[0]", {"mobile": int(mobile)})),
        "mongo-bytes": list(scan_mod.flatten("mongo", "documents.chunks[0]", {"data": email.encode()})),
        "logs": list(scan_mod.flatten_json_lines("logs", "api.log", [f"INFO sent to {mobile}"])),
        "logs-json": list(scan_mod.flatten_json_lines("logs", "c.log", [json.dumps({"m": {"to": pan}})])),
        "jaeger": list(scan_mod.flatten("jaeger", "trace[0]", {"spans": [{"tags": [{"value": pan}]}]})),
        "outbox": list(scan_mod.flatten("mongo", "mock_outbox[0]", {"text": f"Hi, your number {mobile}"})),
        "events": list(scan_mod.flatten("mongo", "events[0]", {"payload": {"email": email}})),
        "llm": list(scan_mod.flatten("llm", "call[0].messages[1]", {"content": f"PAN is {pan}"})),
    }
    found = {name: _raw_labels(_scan(*items)) for name, items in sources.items()}
    assert all(found.values()), found


def test_a_value_the_fixture_does_not_know_is_caught_by_the_detectors() -> None:
    result = _scan(_item("a model made up 9123456780 for the hire", source="llm", location="call[0]"))
    assert [(h.kind, h.label, h.source) for h in result.hits] == [("detector", "MOBILE", "llm")]


@pytest.mark.parametrize(
    "text",
    [
        "run_6f1e9876543210abc1234def",  # a 10-digit run inside a run id (RUN-3's false [MOBILE])
        "trace_id=4bf92f3577b34da6a3ce9876543210ab",
        "appr_0123456789abcdef9876543210",
        "e7f3a9b2-1c4d-4e8f-a6b5-987654321012",
    ],
)
def test_digits_inside_an_opaque_id_are_an_id_not_a_value(text: str) -> None:
    result = _scan(_item(text))
    assert result.clean, [h.describe() for h in result.hits]
    assert result.ids_excused >= 1


def test_digits_next_to_an_id_are_still_a_value() -> None:
    result = _scan(_item("run_abc 9123456780"))
    assert [h.label for h in result.hits] == ["MOBILE"]


def test_a_fixture_value_inside_an_id_is_still_a_leak() -> None:
    result = _scan(_item(f"run_{F['mobile']}abcdef0123"))
    assert "mobile" in _raw_labels(result)


def test_an_allow_rule_excuses_only_its_source_location_and_type() -> None:
    rule = scan_mod.Allow("mongo", r"users\[\d+\]\.email", "EMAIL", "platform users log in by email")
    allowed = _scan(_item("hr@acme.example.com", location="users[0].email"), allow=(rule,))
    elsewhere = _scan(_item("hr@acme.example.com", location="audit_log[0].reason"), allow=(rule,))
    raw = _scan(_item(F["email"], location="users[0].email"), allow=(rule,))
    assert allowed.clean
    assert len(allowed.allowed) == 1
    assert [h.label for h in elsewhere.hits] == ["EMAIL"]
    assert "email" in _raw_labels(raw)  # a fixture value is never allowed


def test_the_report_names_locations_never_values() -> None:
    result = _scan(_item(f"x {F['pan']} {F['mobile']}", location="events[2].payload"))
    report = json.dumps(result.report())
    assert result.report()["result"] == "LEAK"
    assert all(value not in report for value in FIXTURE.values())
    assert "events[2].payload" in report


def test_a_blank_fixture_value_is_refused() -> None:
    with pytest.raises(ValueError, match="blank"):
        scan_mod.raw_patterns({"mobile": " "})


def test_a_json_log_line_is_scanned_field_by_field_and_a_plain_line_whole() -> None:
    lines = [json.dumps({"msg": "boot", "limit": 7012345678}), f"plain {F['pan']}"]
    items = list(scan_mod.flatten_json_lines("container_logs", "caddy", lines))
    assert {i.location for i in items} == {"caddy:1.<key>", "caddy:1.msg", "caddy:1.limit", "caddy:2"}
    rule = scan_mod.Allow("container_logs", r"caddy:\d+\.limit", "MOBILE", "a byte count")
    result = scan_mod.scan(items, FIXTURE, (rule,))
    assert [(h.label, h.location) for h in result.hits] == [("pan", "caddy:2"), ("PAN", "caddy:2")]
