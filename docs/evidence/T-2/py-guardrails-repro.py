"""Reproduce the py-guardrails property-test counterexample found during T-2's `make check`."""

from onb_guardrails.pii import redact

text = "_account number 000000000"
print("input   :", repr(text))
print("redacted:", repr(redact(text)))
