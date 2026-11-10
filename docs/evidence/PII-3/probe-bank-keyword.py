"""Exhaustive probe of the bank-account detector family (fix after merge).

Every account keyword spelling x suffix x glue before/after; reports any digit that survives redact().
Run: uv run --frozen python docs/evidence/PII-3/probe-bank-keyword.py
"""

import itertools

from onb_guardrails.pii import redact

KW = ["account", "Account", "ACCOUNT", "acct", "acc", "a/c", "A/C"]
SUFFIX = ["", " no", "_no", " number", "_number", " num", " no.", ".no", " #", "_#", " no is", "_no_is", " is"]
PRE = ["", "_", "x", "X_", "__", "-", ".", "my", "(", " ", "​"]
SEP = ["", " ", "_", ": ", "_:_", " - ", "#", "\n", "__", " _ ", "\t"]
POST = ["", "_", "x", ".", ")", " ok"]

inputs = [f"{p}{k}{s}{sep}000111222{q}" for p, k, s, sep, q in itertools.product(PRE, KW, SUFFIX, SEP, POST)]
leaks = [text for text in inputs if any(c.isdigit() for c in redact(text))]
print(f"{len(inputs)} inputs, {len(leaks)} leaks")
for text in leaks[:60]:
    print(repr(text))
