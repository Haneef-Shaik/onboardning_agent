"""The `make test-pii` reports (SEC-1): JSON for machines, Markdown for the evidence folder. They hold
counts, locations, types and reasons only, never a value."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .scan import ScanResult
from .scenario import Journey

SCAN_NAME = "pii-scan-report"


def write_scan_report(directory: Path, result: ScanResult, flow: dict[str, Any], journey: Journey) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    body = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "what": "SEC-1 make test-pii: full seed-template run with synthetic fixture PII, then a scan",
        "run": {"run_id": journey.run_id, "hire_id": journey.hire_id, "tasks": dict(journey.tasks)},
        "evidence_of_flow": flow,
        **result.report(),
        "allow_rules": [
            {"source": a.source, "location": a.location, "type": a.pii_type, "reason": a.reason}
            for a in result.allow_rules
        ],
    }
    (directory / f"{SCAN_NAME}.json").write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")
    (directory / f"{SCAN_NAME}.md").write_text(_markdown(body), encoding="utf-8")
    return directory / f"{SCAN_NAME}.json"


def _markdown(body: dict[str, Any]) -> str:
    lines = [
        "# PII leak scan (SEC-1, `make test-pii`)",
        "",
        f"Generated {body['generated_at']}. Result: **{body['result']}**, hits: **{body['hits']}**.",
        "",
        f"Run `{body['run']['run_id']}` for hire `{body['run']['hire_id']}`.",
        "",
        "## Evidence that the values flowed",
        "",
        "| Check | Value |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in body["evidence_of_flow"].items()),
        "",
        "## Items scanned by source",
        "",
        "| Source | Items |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in body["items_scanned_by_source"].items()),
        f"| **total** | **{body['items_scanned']}** |",
        "",
        f"Raw values searched: {', '.join(body['raw_values_searched'])} (each in its written forms), plus every "
        "PII-3 detector type.",
        "",
        "Detector hits wholly inside opaque ids (hex, `ptk_`, UUID), not counted: "
        f"{body['detector_hits_inside_opaque_ids']}.",
        "",
        "Redaction markers glued into ids (over-redaction by the OBS-1 scrubber, not a leak): "
        f"{body['markers_glued_into_ids_by_source'] or 'none'}.",
        "",
        "## Allowed detector hits",
        "",
        "| Source | Type | Count | Reason |",
        "|---|---|---|---|",
        *(f"| {a['source']} | {a['type']} | {a['count']} | {a['reason']} |" for a in body["detector_hits_allowed"]),
        "",
        "## Findings",
        "",
        *(
            [f"- {f['kind']} `{f['label']}` in {f['source']} at `{f['location']}`" for f in body["findings"]]
            or ["None."]
        ),
        "",
    ]
    return "\n".join(lines)
