# Checkpoint — AGT-1

Steps done: agent input refs + variables (ADR 0021); templates_agent reads new_hires; real-stack integration tests (boxes 2, 3); eval self-test; 11/11 mutations; reviews; `make check` green; handoff attempt 1.
Next: goal-verifier attempt 1. Then board status. Box 1 waits on OW-1 (real-connector eval report); if it is the only failing box, set `on hold` with note "waiting on OW-1 for real-connector eval report; all other boxes verified".
Surprises: mocks need 24-hex ids (task/hire ids are slugs/`h_…`); templates_agent could not read new_hires.
