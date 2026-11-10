# Acceptance ledger

The release evidence for the Onboarding Platform MVP. There is one row for each PRD user story
([§4](../PRD.md#4-key-user-stories)), non-functional target
([§12](../PRD.md#12-non-functional-requirements)), success metric
([§13](../PRD.md#13-success-metrics)), per-agent eval gate ([§8.2](../PRD.md#82-agent-evaluations-deterministic--no-llm-as-judge))
and external decision.

Each row names the goals that prove it and the milestone gate that may not be declared until the row
passes. Goal status lives on the board; this file holds acceptance results only.

## How the ledger is kept

- **Proved by** lists the goals whose Done-when boxes demonstrate the row. The goal named **last**
  runs the gate-level proof and is the **only** goal that writes the row's result. Earlier goals put
  their evidence in their own handoff.
- **Gate** is the milestone that waits on the row. `PROD` means "before any production use with real
  people's data"; it is outside the MVP and no milestone waits on it.
- **Result** is one of:
  - `open`
  - `pass · YYYY-MM-DD · <environment: compose | VM> · <evidence path or run link>`
  - `fail · YYYY-MM-DD · <environment> · <evidence>`
  - `n/a · <reason>`, only where the PRD allows it.
- A result changes only on a goal branch, through `/run-goal` and `goal-verifier`. Never edit a
  result on `main` by hand.

`python3 scripts/plan/plan.py check` (inside `make check`) fails if a row names a goal that doesn't
exist, if its gate doesn't wait (directly or through other goals) on every goal in *Proved by*, or if
a result is malformed.

## User stories (PRD §4)

| ID | Requirement | Proved by | Gate | Result |
|---|---|---|---|---|
| U1 | HR describes a flow in plain language and gets a draft template | TB-1, UI-2, E2E-1 | MS-6 | open |
| U2 | HR reviews the draft as a visual DAG, edits tasks/rules, sends it for publishing | TPL-2, UI-2, E2E-1 | MS-6 | open |
| U3 | HR adds a new hire, picks a template, and onboarding starts automatically | HIRE-1, RUN-1, UI-4, E2E-1 | MS-6 | open |
| U4 | HR sees every run's progress, which task is blocked and why | RUN-2, UI-5 | MS-5 | open |
| U5 | Manager approves/rejects from the web inbox or the Telegram mock with an AI-prepared summary | HIL-1, NOTIF-1, UI-6, E2E-1 | MS-6 | open |
| U6 | HR asks "why is Priya's onboarding stuck?" and gets a grounded answer | CHAT-1, UI-7 | MS-5 | open |
| U7 | HR_ADMIN opens a run's trace and sees every agent, LLM and tool call | F-4, RUN-3, UI-5, DEMO-1 | MS-6 | open |
| U8 | HR_ADMIN sees which agent may access which MCP tools | REG-1, HUB-1, UI-8 | MS-5 | open |
| U9 | Manager proposes a template change that HR can adopt | TB-2, UI-3, E2E-1 | MS-6 | open |
| U10 | HR_ADMIN switches an agent's model/connector via configuration | LLM-1, DEMO-1 | MS-6 | open |

## Non-functional targets (PRD §12)

| ID | Requirement | Proved by | Gate | Result |
|---|---|---|---|---|
| NF-TENANT | Cross-tenant suite passes 100% for all endpoints, MCP tools and consumers; runs in CI | T-3, HUB-1, EVT-1, SEC-2 | MS-6 | open |
| NF-PII | 0 raw sensitive values outside the vault: prompts, outputs, logs, traces | OBS-1, GR-1, NOTIF-1, SEC-1 | MS-6 | open |
| NF-PII-INT | 100% of tampered vault records and forged or widened grants rejected | PII-2, PII-4, SEC-1 | MS-6 | open |
| NF-EVAL | Eval gates met before any agent, prompt or model change merges | EVAL-1, EVAL-2 | MS-6 | open |
| NF-DUR | No run state lost on worker restart; jobs at-least-once with idempotent handlers | EVT-1, RUN-2 | MS-4 | open |
| NF-LAT | API p95 < 300 ms (non-LLM); approval decision → run resumed < 5 s | HIL-1, PERF-1 | MS-6 | open |
| NF-SEC | No secrets in code; agent tokens ≤ 15 min; inputs validated at boundaries | HUB-1, SEC-2 | MS-6 | open |
| NF-OBS | 100% of LLM and tool calls traced; every run has a trace link | F-4, RUN-3, UI-5, DEMO-1 | MS-6 | open |
| NF-PORT | Switching an agent's model/connector requires config only | LLM-1, DEMO-1 | MS-6 | open |
| NF-QUAL | ≥ 80% coverage on Python libs; E2E for the golden path | F-1, E2E-1 | MS-6 | open |

## Success metrics (PRD §13)

| ID | Requirement | Proved by | Gate | Result |
|---|---|---|---|---|
| SM-1 | Golden path demo on the VM, full trace in Jaeger with no PII | E2E-1, SEC-1, DEMO-1 | MS-6 | open |
| SM-2 | ≥ 70% of seed-template tasks complete without human action (excluding approvals) | AGT-1, DEMO-1 | MS-6 | open |
| SM-3 | 0 successful cross-agent or cross-tenant access; 100% of attempts logged | HUB-1, T-3, DEMO-1 | MS-6 | open |
| SM-4 | Model swap: change the `standard` alias, the next task uses the new model | LLM-1, RUN-3, DEMO-1 | MS-6 | open |

## Agent eval gates (PRD §8.2)

Safety 100% pass^3; capability ≥ 90% pass@1 and ≥ 80% pass^3, on the real connector.

| ID | Requirement | Proved by | Gate | Result |
|---|---|---|---|---|
| EV-TB | `template_builder` draft + suggest (`tb-*`, `tbs-*`) meets the gates | TB-1, TB-2, EVAL-2 | MS-6 | open |
| EV-AP | `approvals_agent` (`ap-*`) meets the gates | HIL-1, EVAL-2 | MS-6 | open |
| EV-DB | `db_agent` (`db-*`) meets the gates | AGT-1, EVAL-2 | MS-6 | open |
| EV-TP | `templates_agent` (`tp-*`) meets the gates | AGT-1, EVAL-2 | MS-6 | open |
| EV-RA | `run_assistant` (`ra-*`) meets the gates | CHAT-1, EVAL-2 | MS-6 | open |

## External decisions and owner actions

| ID | Requirement | Proved by | Gate | Result |
|---|---|---|---|---|
| DEC-1 | Bedrock access and model ids for `fast` / `standard` / `deep` | OW-1 | MS-3 | open |
| DEC-2 | VM and domain for the deployment | OW-2 | MS-6 | open |
| DEC-3 | Legal review of Aadhaar (UIDAI) and DPDP Act 2023 obligations | — | PROD | open |
| DEC-4 | PII keys moved from env to a KMS/HSM | — | PROD | open |
| DEC-5 | CI host chosen and recorded | F-3 | MS-0 | open |
