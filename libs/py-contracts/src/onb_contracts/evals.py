"""Eval run contracts (PRD §9, §10 `GET /evals/runs`, `GET /evals/runs/{id}`, HR_ADMIN, read-only).

A run is the summary the eval harness (`onb_evals.results.record_run`) stores in `eval_runs` under the
org that owns the results: which agent and model were evaluated, on which dataset and fixture, the
metrics per agent and category, the gates and each case's pass count. Per-repetition check details
stay in the report file. Gate details are redacted by the harness before they are stored.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from onb_contracts.envelope import Envelope

__all__ = [
    "DEFAULT_EVAL_RUN_LIMIT",
    "EVAL_AGENT_PATTERN",
    "EVAL_RUN_ID_PATTERN",
    "MAX_EVAL_RUN_LIMIT",
    "MAX_EVAL_RUN_SKIP",
    "EvalCaseSummary",
    "EvalGate",
    "EvalMetrics",
    "EvalRun",
    "EvalRunEnvelope",
    "EvalRunSummary",
    "EvalRunsEnvelope",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# `onb_evals.session._run_id`: `evr_<UTC stamp>_<8 hex>`.
EVAL_RUN_ID_PATTERN = r"^evr_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}$"
# An agent name, or `all` for a self-test run.
EVAL_AGENT_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_EVAL_RUN_LIMIT = 100
DEFAULT_EVAL_RUN_LIMIT = 20
# Paging reaches at most this many runs deep ((page - 1) * limit).
MAX_EVAL_RUN_SKIP = 10_000


class EvalMetrics(BaseModel):
    """One (agent, category) group. Rates are 0..1, or null when the group has nothing to rate."""

    model_config = _FROZEN

    cases: int
    repetitions: int
    pass_at_1: float | None
    pass_hat_k: float | None
    tool_call_accuracy: float | None
    avg_steps: float
    p50_latency_ms: float
    p95_latency_ms: float
    avg_tokens: float


class EvalGate(BaseModel):
    model_config = _FROZEN

    name: str
    agent: str
    passed: bool
    detail: str


class EvalCaseSummary(BaseModel):
    model_config = _FROZEN

    case_id: str
    agent: str
    category: str
    passes: int
    repetitions: int


class EvalRunSummary(BaseModel):
    """A run in the list: what was evaluated and whether its gates passed."""

    model_config = _FROZEN

    id: str
    mode: str
    agent: str
    llm: str
    dataset: str
    fixture: str
    repeat: int
    passed: bool
    all_cases_passed: bool
    started_at: datetime
    finished_at: datetime


class EvalRun(EvalRunSummary):
    """One run in full: `metrics[agent][category]`, the gates, and each case's passes."""

    metrics: dict[str, dict[str, EvalMetrics]]
    gates: list[EvalGate]
    cases: list[EvalCaseSummary]


class EvalRunsEnvelope(Envelope[list[EvalRunSummary]]):
    """The org's eval runs, newest first, paged."""


class EvalRunEnvelope(Envelope[EvalRun]):
    """One eval run."""
