"""UI-8: the API's eval run read model matches what the eval harness writes.

Apps may not import the harness (`libs/py-evals/tests/test_harness_boundary.py`), so the API's tests
write runs through `onb_api.testing_evals`. These checks, outside the apps, hold that helper, the
collection name and the run id grammar to the harness's own writer. They need no database: both
writers run over the API's in-memory driver stand-in.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from onb_api.evals.store import EVAL_RUNS_COLLECTION, run_from_document
from onb_api.testing_evals import eval_report, store_eval_run
from onb_api.testing_mongo import MemoryDatabase, memory_store
from onb_contracts.evals import EVAL_RUN_ID_PATTERN
from onb_evals.results import EVAL_RUNS_COLLECTION as HARNESS_COLLECTION
from onb_evals.results import record_run
from onb_evals.session import _run_id

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RUN = "evr_20261102T090000Z_0000aaa1"


def test_the_api_reads_the_collection_the_harness_writes() -> None:
    assert EVAL_RUNS_COLLECTION == HARNESS_COLLECTION


def test_the_run_id_pattern_matches_the_harness_ids() -> None:
    for moment in (T0, datetime(2027, 1, 31, 23, 59, 59, tzinfo=UTC)):
        assert re.fullmatch(EVAL_RUN_ID_PATTERN, _run_id(moment))


def test_the_test_helper_stores_exactly_what_the_harness_stores() -> None:
    report = eval_report(RUN, T0, T0 + timedelta(minutes=4), passed=False)
    harness, helper = MemoryDatabase(), MemoryDatabase()

    async def main() -> None:
        await record_run(memory_store(harness), "org_acme", report)
        await store_eval_run(memory_store(helper), "org_acme", report)

    asyncio.run(main())
    assert harness["eval_runs"].documents == helper["eval_runs"].documents
    run = run_from_document(harness["eval_runs"].documents[0])
    assert (run.id, run.passed, [c.passes for c in run.cases]) == (RUN, False, [3, 2])
