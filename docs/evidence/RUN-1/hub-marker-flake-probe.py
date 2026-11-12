"""Probe: how often a random 12-hex marker (HUB-1's test) is counted as PII by the guardrail scanner."""

import secrets

from onb_mcp_hub.pii import count_pii

hits = []
for _ in range(20000):
    marker = secrets.token_hex(6)
    if count_pii(marker, "marker"):
        hits.append(marker)
print(f"{len(hits)} of 20000 random markers counted as PII ({len(hits) / 200:.2f}%); e.g. {hits[:5]}")
