"""The OpenAPI export the TS client is generated from (PRD §11 Contracts)."""

import json
from pathlib import Path

import pytest
from onb_api.openapi import export_openapi, main


def test_export_is_deterministic_and_ends_with_a_newline() -> None:
    first = export_openapi()
    assert first == export_openapi()
    assert first.endswith("}\n")


def test_export_has_sorted_keys_so_diffs_stay_small() -> None:
    spec = json.loads(export_openapi())
    assert list(spec) == sorted(spec)
    assert list(spec["components"]["schemas"]) == sorted(spec["components"]["schemas"])


def test_schema_names_are_the_readable_envelope_names() -> None:
    names = set(json.loads(export_openapi())["components"]["schemas"])
    assert {"HealthEnvelope", "ErrorEnvelope", "ErrorBody", "ErrorCode", "Meta", "PageMeta"} <= names
    assert not [n for n in names if "_" in n], f"generated generic names leaked: {names}"


def test_fastapi_validation_schemas_are_not_exported() -> None:
    # Validation errors leave as ErrorEnvelope, so FastAPI's HTTPValidationError must not appear.
    names = set(json.loads(export_openapi())["components"]["schemas"])
    assert "HTTPValidationError" not in names
    assert "ValidationError" not in names


def test_main_writes_the_export_to_the_given_path(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "openapi.json"
    assert main([str(out)]) == 0
    assert out.read_text(encoding="utf-8") == export_openapi()


def test_main_needs_exactly_one_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
