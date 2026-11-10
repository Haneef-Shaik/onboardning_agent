"""EVAL-1 boxes 2 and 4 on the compose stack: `make evals-selftest` runs this.

It calls the CLI (`python -m onb_evals selftest`, in process) with the compose Mongo: all 27 cases against
scripted outputs on the `fake` connector, k=3, a fresh database per repetition, negative controls. The
run must pass, write its report to `evals/reports/`, insert an `eval_runs` record, stamp every span
with `eval.run_id`/`eval.case_id`, and leave no `onb_eval_*` database behind.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from onb_evals.cli import ENV_MONGO_URI, ENV_RESULTS_DATABASE, ENV_RESULTS_ORG, main
from onb_evals.databases import DATABASE_PREFIX
from onb_evals.scripted import find_repo_file
from onb_evals.spans import EVAL_CASE_ID, EVAL_RUN_ID
from onb_telemetry.testing import captured_telemetry
from pymongo import MongoClient

# The `unscoped` fixture (T-2 safety net): `with unscoped("why"):` for a root read-back across orgs.
Unscoped = Callable[[str], AbstractContextManager[None]]

RESULTS_ORG = "org_eval_selftest"


def _printed(pattern: str, out: str) -> str:
    match = re.search(pattern, out)
    assert match, f"the CLI did not print {pattern!r}"
    return match.group(1)


def test_make_evals_selftest_passes_all_27_cases_and_records_the_run(
    mongo_uri: str, root_client: MongoClient[dict[str, Any]], capsys: pytest.CaptureFixture[str], unscoped: Unscoped
) -> None:
    results_db = f"onb_evalresults_{secrets.token_hex(4)}"
    env = {**os.environ, ENV_MONGO_URI: mongo_uri, ENV_RESULTS_DATABASE: results_db, ENV_RESULTS_ORG: RESULTS_ORG}
    try:
        with captured_telemetry() as telemetry:
            code = main(["selftest", "--repeat", "3"], env=env)
            spans = telemetry.spans()
        out = capsys.readouterr().out
        print(out)  # the summary lands in the make output (pytest -s)
        assert code == 0, out
        run_id = _printed(r"eval_runs: (\S+)", out)
        report_path = find_repo_file("evals/reports") / _printed(r"report: .*/(\S+\.json)", out)
        report = json.loads(report_path.read_text())
        assert report["run_id"] == run_id
        assert len(report["cases"]) == 27
        assert report["all_cases_passed"]
        assert report["passed"]
        assert len(report["negative_controls"]) == 8
        assert all(control["caught"] for control in report["negative_controls"])

        with unscoped("root read-back by id, without assuming the org, proves which org the run was recorded under"):
            record = root_client[results_db]["eval_runs"].find_one({"_id": run_id})
        assert record is not None
        assert record["org_id"] == RESULTS_ORG
        assert record["mode"] == "selftest"
        assert len(record["summary"]) == 27

        case_spans = [s for s in spans if (s.attributes or {}).get(EVAL_RUN_ID) == run_id]
        assert {(s.attributes or {}).get(EVAL_CASE_ID) for s in case_spans} >= {c["case_id"] for c in report["cases"]}
        assert all(
            (s.attributes or {}).get(EVAL_RUN_ID) == run_id for s in spans if s.name in {"llm.chat", "eval.tool"}
        )

        leftovers = [n for n in root_client.list_database_names() if n.startswith(DATABASE_PREFIX)]
        assert leftovers == []
    finally:
        root_client.drop_database(results_db)
