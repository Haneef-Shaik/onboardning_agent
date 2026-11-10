"""The deterministic check catalog (evals/README.md "Check catalog"). Graded by code only.

Importing this package registers every check; `run_check(spec, context)` grades one.
"""

from onb_evals.checks import outcome, safety, template, trajectory
from onb_evals.checks.base import CheckResult, registered, run_check

__all__ = ["CheckResult", "outcome", "registered", "run_check", "safety", "template", "trajectory"]
