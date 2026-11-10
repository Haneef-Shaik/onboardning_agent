"""Run reports (evals/README.md: `evals/reports/<timestamp>-<agent>-<model>.{json,md}`, git-ignored).

Exact rates are kept next to the floats (`pass_at_1_exact: "9/10"`), so a later run can use a report as
its regression baseline without rounding. Every free-text detail passes the PII-3 redactor before it is
written: a failing check may quote the agent's output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from onb_guardrails.pii import redact

from onb_evals.gates import GateResult
from onb_evals.metrics import ALL, CaseResult, Metrics, metrics_by_group

__all__ = ["REPORT_SCHEMA", "RunInfo", "baseline_from_report", "build_report", "report_stem", "write_report"]

REPORT_SCHEMA: Final = "onb-eval-report/1"
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class RunInfo:
    run_id: str
    started_at: datetime
    finished_at: datetime
    agent: str
    llm: str
    dataset: str
    fixture: str
    repeat: int
    mode: str


def _rate(value: Fraction | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _metrics(metrics: Metrics) -> dict[str, Any]:
    return {
        "cases": metrics.cases,
        "repetitions": metrics.repetitions,
        "pass_at_1": _rate(metrics.pass_at_1),
        "pass_at_1_exact": str(metrics.pass_at_1),
        "pass_hat_k": _rate(metrics.pass_hat_k),
        "pass_hat_k_exact": str(metrics.pass_hat_k),
        "tool_call_accuracy": _rate(metrics.tool_call_accuracy),
        "avg_steps": round(metrics.avg_steps, 2),
        "p50_latency_ms": round(metrics.p50_latency_ms, 1),
        "p95_latency_ms": round(metrics.p95_latency_ms, 1),
        "avg_tokens": round(metrics.avg_tokens, 1),
    }


def _case(result: CaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "agent": result.agent,
        "category": result.category,
        "pass_rate": _rate(result.pass_rate),
        "passed_all": result.passed_all,
        "repetitions": [
            {
                "repetition": rep.repetition,
                "passed": rep.passed,
                "steps": rep.steps,
                "input_tokens": rep.input_tokens,
                "output_tokens": rep.output_tokens,
                "latency_ms": round(rep.latency_ms, 1),
                "error": None if rep.error is None else redact(rep.error),
                "checks": [{"type": c.type, "passed": c.passed, "detail": redact(c.detail)} for c in rep.checks],
            }
            for rep in result.repetitions
        ],
    }


def build_report(info: RunInfo, results: Sequence[CaseResult], gates: Iterable[GateResult]) -> dict[str, Any]:
    gate_list = [{"name": g.name, "agent": g.agent, "passed": g.passed, "detail": redact(g.detail)} for g in gates]
    metrics: dict[str, dict[str, Any]] = {}
    for (agent, category), values in metrics_by_group(results).items():
        metrics.setdefault(agent, {})[category] = _metrics(values)
    return {
        "schema": REPORT_SCHEMA,
        "run_id": info.run_id,
        "mode": info.mode,
        "started_at": info.started_at.isoformat(),
        "finished_at": info.finished_at.isoformat(),
        "agent": info.agent,
        "llm": info.llm,
        "dataset": info.dataset,
        "fixture": info.fixture,
        "repeat": info.repeat,
        "passed": bool(gate_list) and all(g["passed"] for g in gate_list),
        "all_cases_passed": all(r.passed_all for r in results),
        "metrics": metrics,
        "gates": gate_list,
        "cases": [_case(r) for r in results],
    }


def report_stem(info: RunInfo) -> str:
    stamp = info.started_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_UNSAFE.sub('_', info.agent)}-{_UNSAFE.sub('_', info.llm)}"


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Eval run {report['run_id']} ({report['mode']})",
        "",
        f"- Agent: {report['agent']} · LLM: {report['llm']} · repeat: {report['repeat']}",
        f"- Dataset: {report['dataset']} · fixture: {report['fixture']}",
        f"- Started {report['started_at']} · finished {report['finished_at']}",
        f"- Gates: **{'PASS' if report['passed'] else 'BLOCKED'}** · every case passed every repetition: "
        f"{'yes' if report['all_cases_passed'] else 'no'}",
        "",
        "| agent | category | cases | pass@1 | pass^k | tool-call acc. | avg steps | p50 ms | p95 ms | avg tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for agent, groups in report["metrics"].items():
        for category, m in groups.items():
            lines.append(
                f"| {agent} | {category} | {m['cases']} | {m['pass_at_1']} | {m['pass_hat_k']} | "
                f"{m['tool_call_accuracy']} | {m['avg_steps']} | {m['p50_latency_ms']} | {m['p95_latency_ms']} | "
                f"{m['avg_tokens']} |"
            )
    lines += ["", "| gate | agent | result | detail |", "|---|---|---|---|"]
    lines += [
        f"| {g['name']} | {g['agent']} | {'PASS' if g['passed'] else 'BLOCK'} | {g['detail']} |"
        for g in report["gates"]
    ]
    failing = [c for c in report["cases"] if not c["passed_all"]]
    lines += ["", f"## Failing cases ({len(failing)})", ""]
    for case in failing:
        for rep in case["repetitions"]:
            bad = [f"{c['type']}: {c['detail']}" for c in rep["checks"] if not c["passed"]]
            if rep["error"]:
                bad.insert(0, f"error: {rep['error']}")
            if bad:
                lines.append(f"- {case['case_id']} #{rep['repetition']}: " + "; ".join(bad))
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], info: RunInfo, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = report_stem(info)
    if (directory / f"{stem}.json").exists():
        stem = f"{stem}-{_UNSAFE.sub('_', info.run_id)}"
    json_path, md_path = directory / f"{stem}.json", directory / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, md_path


def baseline_from_report(path: Path) -> dict[tuple[str, str], Fraction]:
    """`(agent, category) → pass@1` from an earlier report, for the regression gate."""
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"{path} is not an eval report ({REPORT_SCHEMA})")
    return {
        (agent, category): Fraction(values["pass_at_1_exact"])
        for agent, groups in report["metrics"].items()
        for category, values in groups.items()
        if category != ALL
    }
