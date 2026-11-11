"""Templates as safe task graphs (PRD §5.1, §6.1 FR-T3).

    validator = TemplateValidator.from_config(Path("config"))
    report = validator.validate(yaml.safe_load(Path("templates/engineer-india.yaml").read_text()))
    assert report.ok, report.issues

The schema is `onb_contracts.templates.Template`; `parse_when` parses a task's `when` condition with
no `eval`, and its `evaluate(variables)` is what the DAG compiler (DAG-1) prunes with.
"""

from .expr import ExpressionError, ExpressionReason, WhenExpression, parse_when
from .validator import Issue, Rule, TemplateInvalidError, TemplateReport, TemplateValidator

__all__ = [
    "ExpressionError",
    "ExpressionReason",
    "Issue",
    "Rule",
    "TemplateInvalidError",
    "TemplateReport",
    "TemplateValidator",
    "WhenExpression",
    "parse_when",
]
