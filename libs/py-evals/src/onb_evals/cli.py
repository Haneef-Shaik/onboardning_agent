"""`python -m onb_evals run --agent <name> --llm <alias|connector:model> --repeat 3` and `… selftest`.

Environment (values come from `.env` or the shell, never from code):
- `EVAL_MONGO_URI` — the MongoDB server; every repetition gets its own `onb_eval_*` database there.
- `EVAL_RESULTS_DATABASE` — the database that keeps `eval_runs`.
- `EVAL_RESULTS_ORG_ID` — the org the `eval_runs` records belong to (HR_ADMIN of that org reads them).
- `LLM_*` / `AWS_*` — the model, through `config/llm.yaml` (LLM-1). The self-test forces `fake`.
- `OTEL_*` — span export (OBS-1); spans carry `eval.run_id` and `eval.case_id`.

Exit codes: 0 gates met (and, for the self-test, every case passed and every control was caught),
1 not met, 2 the run could not start.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from onb_data import connect
from onb_llm import LLMError, LLMRuntime, discover_connectors, load_llm_config
from onb_telemetry import setup as telemetry_setup
from onb_telemetry.settings import TelemetrySettings

from onb_evals.adapters import AdapterNotFoundError
from onb_evals.cases import CaseFormatError
from onb_evals.databases import ThrowawayDatabases
from onb_evals.fixture import FixtureError
from onb_evals.report import baseline_from_report
from onb_evals.scripted import fake_runtime, find_repo_file
from onb_evals.session import RunOutcome, SelftestPaths, SessionError, run_eval, run_selftest
from onb_evals.spans import EvalSpans

__all__ = ["ENV_MONGO_URI", "ENV_RESULTS_DATABASE", "ENV_RESULTS_ORG", "main", "parse_args"]

ENV_MONGO_URI: Final = "EVAL_MONGO_URI"
ENV_RESULTS_DATABASE: Final = "EVAL_RESULTS_DATABASE"
ENV_RESULTS_ORG: Final = "EVAL_RESULTS_ORG_ID"
_REQUIRED: Final = (ENV_MONGO_URI, ENV_RESULTS_DATABASE, ENV_RESULTS_ORG)
_STARTUP_ERRORS: Final = (
    SessionError,
    AdapterNotFoundError,
    CaseFormatError,
    FixtureError,
    LLMError,
    OSError,
    ValueError,
)


def _positive(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="onb_evals", description="Deterministic agent evals (PRD §8.2).")
    parser.add_argument("--root", type=Path, default=None, help="repository root (default: found from the cwd)")
    parser.add_argument("--reports-dir", type=Path, default=None, help="default: <root>/evals/reports")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="one agent's cases against a configured model")
    run.add_argument("--agent", required=True)
    run.add_argument("--llm", required=True, help="an alias from config/llm.yaml, or connector:model")
    run.add_argument("--repeat", type=_positive, default=3)
    run.add_argument("--dataset", type=Path, default=None)
    run.add_argument("--fixture", type=Path, default=None)
    run.add_argument("--baseline", type=Path, default=None, help="an earlier report for the regression gate")
    selftest = commands.add_parser("selftest", help="every case against scripted outputs, fake connector")
    selftest.add_argument("--repeat", type=_positive, default=3)
    return parser.parse_args(list(argv))


def _root(args: argparse.Namespace) -> Path:
    if args.root is not None:
        return Path(args.root)
    return find_repo_file("evals/datasets", Path.cwd()).parents[1]


def _start_spans(env: Mapping[str, str]) -> tuple[EvalSpans, bool]:
    """Our span hook on the active pipeline; initialises telemetry when nothing has yet."""
    spans = EvalSpans()
    try:
        telemetry_setup.register_span_processor(spans)
        return spans, False
    except RuntimeError:
        telemetry_setup.init_telemetry(
            "onb-evals", processors=[spans], settings=TelemetrySettings.from_env(env), instrument=False
        )
        return spans, True


def _summary(outcome: RunOutcome) -> str:
    report = outcome.report
    controls = report.get("negative_controls")
    caught = "" if controls is None else f", controls caught {sum(c['caught'] for c in controls)}/{len(controls)}"
    cases = len(report["cases"])
    return (
        f"{report['mode']} {report['run_id']}: {cases} cases x {report['repeat']}, "
        f"all cases passed: {report['all_cases_passed']}, gates: {'PASS' if report['passed'] else 'BLOCKED'}"
        f"{caught}\nreport: {outcome.paths[0]}\nreport: {outcome.paths[1]}\neval_runs: {outcome.record_id}\n"
        f"result: {'PASS' if outcome.passed else 'FAIL'}"
    )


async def _dispatch(args: argparse.Namespace, env: Mapping[str, str], spans: EvalSpans | None) -> RunOutcome:
    root = _root(args)
    reports = args.reports_dir or root / "evals" / "reports"
    databases = ThrowawayDatabases(env[ENV_MONGO_URI])
    results = connect(env[ENV_MONGO_URI], env[ENV_RESULTS_DATABASE], app_name="onb-evals")
    paths = SelftestPaths.in_repo(root)
    try:
        if args.command == "selftest":
            return await run_selftest(
                databases=databases,
                results_store=results,
                results_org=env[ENV_RESULTS_ORG],
                paths=paths,
                reports_dir=reports,
                runtime=fake_runtime(env),
                repeat=args.repeat,
                spans=spans,
            )
        config = load_llm_config(root / "config" / "llm.yaml", env)
        runtime = LLMRuntime.start(config, discover_connectors(), env)
        return await run_eval(
            agent=args.agent,
            llm=args.llm,
            databases=databases,
            results_store=results,
            results_org=env[ENV_RESULTS_ORG],
            dataset=args.dataset or paths.dataset,
            fixture=args.fixture or paths.fixture,
            reports_dir=reports,
            runtime=runtime,
            repeat=args.repeat,
            baseline=baseline_from_report(args.baseline) if args.baseline else None,
            spans=spans,
        )
    finally:
        await results.close()


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ if env is None else env)
    missing = [name for name in _REQUIRED if not environment.get(name, "").strip()]
    if missing:
        print(f"onb_evals: set {', '.join(missing)} (see libs/py-evals/src/onb_evals/cli.py)", file=sys.stderr)
        return 2
    spans, owned = _start_spans(environment)
    try:
        outcome = asyncio.run(_dispatch(args, environment, spans))
    except _STARTUP_ERRORS as error:
        print(f"onb_evals: {error}", file=sys.stderr)
        return 2
    finally:
        if owned:
            telemetry_setup.active_telemetry().shutdown()
    print(_summary(outcome))
    return 0 if outcome.passed else 1
