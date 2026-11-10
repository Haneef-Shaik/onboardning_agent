"""One eval run end to end: cases → runner → metrics and gates → report file → `eval_runs` record.

`run_selftest` is the CI self-test (PRD §8.2): every case of the dataset against its scripted output on
the `fake` connector, k=3, plus negative controls (scripted bad behaviour that must fail the named
checks), so a self-test that passes shows both that good outputs pass and that the checks bite.
`run_eval` runs one agent's cases through its registered adapter and a configured model.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from onb_data import DataStore
from onb_llm import LLMRuntime

from onb_evals.adapters import RuntimeLLM, adapter_for
from onb_evals.cases import EvalCase, load_cases
from onb_evals.fixture import load_seed
from onb_evals.gates import evaluate_gates
from onb_evals.metrics import CaseResult
from onb_evals.report import RunInfo, build_report, write_report
from onb_evals.results import record_run
from onb_evals.runner import DatabaseProvider, EvalRunner, LLMProvider
from onb_evals.scripted import ScriptedAgent, ScriptedLLM
from onb_evals.spans import EvalSpans

__all__ = [
    "Control",
    "RunOutcome",
    "SelftestPaths",
    "SessionError",
    "load_controls",
    "load_scripts",
    "run_eval",
    "run_selftest",
]

Turns = list[dict[str, Any]]


class SessionError(RuntimeError):
    """The run cannot start: a missing script, a malformed script file, an unknown agent."""


@dataclass(frozen=True, slots=True)
class SelftestPaths:
    dataset: Path
    fixture: Path
    scripts: Path
    controls: Path

    @classmethod
    def in_repo(cls, root: Path) -> SelftestPaths:
        evals = root / "evals"
        return cls(
            dataset=evals / "datasets" / "onboarding_agents_v1.jsonl",
            fixture=evals / "fixtures" / "seed_v1.json",
            scripts=evals / "selftest" / "scripted_outputs_v1.jsonl",
            controls=evals / "selftest" / "negative_controls_v1.jsonl",
        )


@dataclass(frozen=True, slots=True)
class Control:
    case_id: str
    turns: Turns
    expect_failed: tuple[str, ...]
    why: str


@dataclass(frozen=True, slots=True)
class RunOutcome:
    report: dict[str, Any]
    paths: tuple[Path, Path]
    record_id: str
    passed: bool


def _turn(raw: Mapping[str, Any], where: str) -> dict[str, Any]:
    """A fake-connector turn; `output` is sugar for a final turn whose content is that JSON object."""
    if "output" in raw and "content" in raw:
        raise SessionError(f"{where}: a turn has output or content, not both")
    if "output" in raw:
        return {**{k: v for k, v in raw.items() if k != "output"}, "content": json.dumps(raw["output"])}
    return dict(raw)


def _lines(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
                raise SessionError(f"{path}:{number}: each line needs a case_id")
            rows.append((number, row))
    return rows


def load_scripts(path: Path) -> dict[str, Turns]:
    scripts: dict[str, Turns] = {}
    for number, row in _lines(path):
        if row["case_id"] in scripts:
            raise SessionError(f"{path}:{number}: case {row['case_id']} is scripted twice")
        scripts[row["case_id"]] = [_turn(t, f"{path}:{number}") for t in row.get("turns", [])]
    return scripts


def load_controls(path: Path) -> list[Control]:
    controls = []
    for number, row in _lines(path):
        expected = tuple(row.get("expect_failed") or ())
        if not expected:
            raise SessionError(f"{path}:{number}: a control needs expect_failed")
        turns = [_turn(t, f"{path}:{number}") for t in row.get("turns", [])]
        controls.append(Control(row["case_id"], turns, expected, str(row.get("why", ""))))
    return controls


def _run_id(started: datetime) -> str:
    return f"evr_{started:%Y%m%dT%H%M%SZ}_{secrets.token_hex(4)}"


async def _finish(
    *,
    info: RunInfo,
    results: Sequence[CaseResult],
    baseline: Mapping[tuple[str, str], Fraction] | None,
    extra: Mapping[str, Any],
    results_store: DataStore,
    results_org: str,
    reports_dir: Path,
) -> tuple[dict[str, Any], tuple[Path, Path], str]:
    gates = evaluate_gates(results, repeat=info.repeat, baseline=baseline)
    report = {**build_report(info, results, gates), **extra}
    paths = write_report(report, info, reports_dir)
    record_id = await record_run(results_store, results_org, report)
    return report, paths, record_id


async def _controls(
    controls: Sequence[Control], cases: Mapping[str, EvalCase], make_runner: Callable[[Turns], EvalRunner], run_id: str
) -> list[dict[str, Any]]:
    checked = []
    for control in controls:
        [result] = await make_runner(control.turns).run([cases[control.case_id]], repeat=1, run_id=run_id)
        rep = result.repetitions[0]
        failed = {c.type for c in rep.checks if not c.passed}
        caught = not rep.passed and set(control.expect_failed) <= failed
        checked.append(
            {
                "case_id": control.case_id,
                "why": control.why,
                "expect_failed": list(control.expect_failed),
                "failed": sorted(failed),
                "caught": caught,
            }
        )
    return checked


async def run_selftest(
    *,
    databases: DatabaseProvider,
    results_store: DataStore,
    results_org: str,
    paths: SelftestPaths,
    reports_dir: Path,
    runtime: LLMRuntime,
    repeat: int = 3,
    spans: EvalSpans | None = None,
    only: Sequence[str] | None = None,
) -> RunOutcome:
    started = datetime.now(UTC)
    run_id = _run_id(started)
    cases = [c for c in load_cases(paths.dataset) if only is None or c.id in only]
    scripts = load_scripts(paths.scripts)
    missing = [c.id for c in cases if c.id not in scripts]
    if missing:
        raise SessionError(f"no script for {missing} in {paths.scripts}")
    seed = load_seed(paths.fixture)
    agent = ScriptedAgent()

    def runner(llm_for: Callable[[EvalCase], LLMProvider]) -> EvalRunner:
        return EvalRunner(databases=databases, seed=seed, adapter_for=lambda c: agent, llm_for=llm_for, spans=spans)

    results = await runner(lambda c: ScriptedLLM(runtime, scripts[c.id])).run(cases, repeat=repeat, run_id=run_id)
    by_id = {c.id: c for c in cases}
    controls = [c for c in load_controls(paths.controls) if c.case_id in by_id]
    checked = await _controls(controls, by_id, lambda turns: runner(lambda c: ScriptedLLM(runtime, turns)), run_id)
    info = RunInfo(
        run_id,
        started,
        datetime.now(UTC),
        "all",
        "fake:eval-scripted",
        str(paths.dataset.name),
        str(seed["fixture_version"]),
        repeat,
        "selftest",
    )
    report, files, record_id = await _finish(
        info=info,
        results=results,
        baseline=None,
        extra={"negative_controls": checked},
        results_store=results_store,
        results_org=results_org,
        reports_dir=reports_dir,
    )
    passed = bool(report["passed"] and report["all_cases_passed"] and all(c["caught"] for c in checked))
    return RunOutcome(report, files, record_id, passed)


async def run_eval(
    *,
    agent: str,
    llm: str,
    databases: DatabaseProvider,
    results_store: DataStore,
    results_org: str,
    dataset: Path,
    fixture: Path,
    reports_dir: Path,
    runtime: LLMRuntime,
    repeat: int = 3,
    baseline: Mapping[tuple[str, str], Fraction] | None = None,
    spans: EvalSpans | None = None,
) -> RunOutcome:
    """One agent's cases through its registered adapter and the model `llm` names (alias or connector:model)."""
    cases = load_cases(dataset, agent=agent)
    if not cases:
        raise SessionError(f"the dataset has no cases for agent {agent!r}")
    adapter = adapter_for(agent)
    provider = RuntimeLLM(runtime, llm)
    started = datetime.now(UTC)
    run_id = _run_id(started)
    seed = load_seed(fixture)
    runner = EvalRunner(
        databases=databases, seed=seed, adapter_for=lambda c: adapter, llm_for=lambda c: provider, spans=spans
    )
    results = await runner.run(cases, repeat=repeat, run_id=run_id)
    info = RunInfo(
        run_id,
        started,
        datetime.now(UTC),
        agent,
        provider.label,
        dataset.name,
        str(seed["fixture_version"]),
        repeat,
        "full",
    )
    report, files, record_id = await _finish(
        info=info,
        results=results,
        baseline=baseline,
        extra={},
        results_store=results_store,
        results_org=results_org,
        reports_dir=reports_dir,
    )
    return RunOutcome(report, files, record_id, bool(report["passed"]))
