# PRD — Agentic HR Onboarding Platform (MVP)

| | |
|---|---|
| Status | Draft v0.3 — tokenised PII vault, agent evals |
| Date | 2026-11-10 |
| Owner | Haneef Shaik |
| Scope | MVP, single organisation, strictly tenant-isolated, Docker Compose on a VM |

### Changelog
- **v0.3** — Sensitive data moved to a **tokenised PII vault**: agents only ever handle opaque
  tokens; values are resolved at the point of use with a signed grant and integrity-checked vault
  records, so AI can neither read nor manipulate them. Deterministic **agent evals** (no
  LLM-as-judge) with a seed dataset in `evals/`. PII retention 90 days confirmed. Manager
  suggestions get their own HR queue.
- **v0.2** — Pluggable LLM connectors (Bedrock default, creds from env). Telegram mocked;
  MongoDB MCP is the only real integration. Sensitive data defined (mobile, email, Aadhaar, PAN,
  bank details) with encryption + masking rules. Managers can suggest template changes.
  Dedicated tenant-isolation design. Deployment = Docker Compose on a VM.
- **v0.1** — First draft after brainstorm.

---

## 1. Problem

HR teams onboard new employees through a long checklist that crosses many systems: collecting
documents, approvals from managers, creating accounts, sending welcome messages, scheduling
orientation. Today this is manual, inconsistent between roles, hard to audit, and nobody can see
where a given hire is stuck. It also involves highly sensitive personal data (Aadhaar, PAN, bank
details) that must be protected.

## 2. Goal

Build a multi-agent onboarding platform where:

1. **HR designs onboarding templates in the UI**, helped by an AI sub-agent; managers can suggest
   changes. Each template becomes a validated task graph (DAG).
2. **An orchestrator runs that DAG for every new hire**, assigning each task to a specialised
   sub-agent that can only touch the tools and data it is registered for.
3. **Humans stay in control**: managers and HR approve sensitive steps; every agent action is
   guarded, logged and traced end to end with OpenTelemetry.
4. **Tenants and personal data are isolated by design**, not by convention.

### Non-goals (MVP)

- New-hire self-service portal (users are HR and managers only).
- Any real integration other than MongoDB — Telegram, Bitbucket, IT provisioning are **mocks**.
- PostgreSQL, Kafka/NATS, SSO, cloud/Kubernetes deployment.
- More than one organisation live (the design supports it; see §7.9).
- Fully autonomous planning — the LLM never invents the run plan (see §5.1).

## 3. Users & roles

| Role | Can do |
|---|---|
| `HR_ADMIN` | Everything HR can, plus publish templates, manage users, view the agent registry and audit log. |
| `HR` | Create new hires, author template drafts with the Template Builder agent, review manager suggestions, start/pause/cancel runs, approve HR-level steps, reveal masked PII (audited), chat with the orchestrator about a run. |
| `MANAGER` | See runs for their direct reports, approve/reject manager-level steps, **suggest template changes** (via the Template Builder in suggest mode), comment. Never sees unmasked PII. |

## 4. Key user stories

| # | As a… | I want to… | So that… |
|---|---|---|---|
| U1 | HR | describe an onboarding flow in plain language ("backend engineer, Bangalore, needs laptop + Bitbucket") and get a draft template | I don't have to build the checklist by hand |
| U2 | HR | review the draft as a visual DAG, edit tasks/rules, and send it for publishing | the flow is correct before anyone uses it |
| U3 | HR | add a new hire and pick a template | onboarding starts automatically |
| U4 | HR | see every run's progress, which task is blocked and why | I can unblock it |
| U5 | Manager | get an approval request (web inbox + Telegram mock) with an AI-prepared summary and approve/reject in one action | I don't slow the hire down |
| U6 | HR | ask the orchestrator "why is Priya's onboarding stuck?" | I get an answer grounded in the run state |
| U7 | HR_ADMIN | open the trace for a run and see every agent, LLM and tool call | I can debug and audit |
| U8 | HR_ADMIN | see which agent may access which MCP tools | access is explicit and reviewable |
| U9 | Manager | propose a change to a template ("add a security-training task for my team") | HR can adopt it without me editing templates directly |
| U10 | HR_ADMIN | switch an agent to a different LLM model/connector via configuration | I can tune cost/quality without a code change |

---

## 5. Product principles & decisions

### 5.1 Template-first, agent-second
- The **template is the source of truth** for which tasks run, in what order, under which rules.
- The orchestrator **compiles** a template version + new-hire variables into a DAG. Tasks whose
  conditions don't match (e.g. `role != engineer` → skip Bitbucket) are pruned at compile time.
- LLM agents do the **fuzzy work inside one task** (drafting messages, summarising for approvers,
  answering questions). They cannot add, remove or reorder tasks in a running DAG.
- A run is pinned to the template **version** it started with.

### 5.2 Least privilege per agent
- Every agent is declared in the **Agent Registry** with an explicit allowlist of MCP servers,
  tools, collections and memory scopes.
- Enforcement happens in the **MCP Hub** (server side), never just in the prompt.
- Agents never hold credentials; the hub injects them.

### 5.3 Guardrails are a layer, not an afterthought
See §8. Every LLM input, LLM output and tool call passes through a guardrail check.

### 5.4 Tenant isolation is enforced at every layer
One organisation in MVP, but isolation is real and tested from day one. See §7.9.

### 5.5 Sensitive data lives only in the vault; everything else carries a token
Raw values exist in one place — the PII vault. Hire records, events, prompts, agent outputs and
traces carry only opaque tokens. A token is resolved only where the value is actually needed, with
a signed grant the AI cannot create or widen, against a vault record whose integrity is verified.
Humans (HR) are the only writers. See §7.10.

### 5.6 LLM connectors are plugins
Agents depend on a connector interface, not on a provider. Bedrock is the default; any
LangChain chat model can be plugged in; the model per agent is configuration. See §7.1.

### 5.7 Everything observable
One OpenTelemetry trace per onboarding run, continued across async waits. 100% of tool/MCP and
LLM calls produce spans.

---

## 6. Functional requirements

### 6.1 Template Studio (agentic)
- **FR-T1** HR chats with the **Template Builder** sub-agent; it returns a structured template
  draft (validated against the template schema) plus a short explanation.
- **FR-T2** Draft is shown as an editable DAG (React Flow) and a form view.
- **FR-T3** Validation on save: acyclic graph, every task's `agent` exists in the registry, every
  referenced tool is in that agent's allowlist, every variable is declared, approval steps have an
  approver rule, no task references sensitive fields in LLM-visible inputs, and every
  `pii_access` declaration is allowed by the field → sink policy (§7.10).
- **FR-T4** Versioned lifecycle `draft → published → archived`. Only `HR_ADMIN` publishes.
- **FR-T5** The Builder agent can only read the registry/catalog and write drafts or change
  requests — it cannot publish or start runs.
- **FR-T6 Manager suggestions.** A manager opens a published template in **suggest mode** and
  chats with the Builder agent, which produces a **change request**: a structured diff against the
  current version + rationale. Stored in `template_change_requests` (`open → accepted | rejected`).
  Change requests land in a dedicated **HR change-request queue** (separate from drafts). HR
  accepts one into a draft (and can edit it) or rejects it with a reason; `HR_ADMIN` publishes.
  The manager is notified of the outcome.

**Template shape (illustrative):**
```yaml
key: engineer-india
version: 3
variables:
  - { name: role, type: enum, values: [engineer, designer, sales] }
  - { name: location, type: string }
  - { name: start_date, type: date }
  - { name: manager_id, type: user_ref }
tasks:
  - id: collect_docs
    title: Collect ID & bank documents
    agent: db_agent
    action: request_documents
    sla: { due: "start_date - 7d" }
  - id: manager_approval
    title: Manager approves equipment & access
    type: approval
    agent: approvals_agent
    approver: { rule: variable, ref: manager_id }
    depends_on: [collect_docs]
  - id: bitbucket_access
    title: Create Bitbucket account (mock)
    agent: provisioning_agent
    action: create_bitbucket_account
    when: "role == 'engineer'"
    depends_on: [manager_approval]
    requires_approval: true
  - id: welcome_message
    title: Send welcome message
    agent: templates_agent
    action: render_and_send
    inputs: { channel: telegram, content_ref: welcome_v2 }
    pii_access: { fields: [mobile], sink: notify.telegram }   # resolved only at the sink
    depends_on: [manager_approval]
```

### 6.2 Onboarding runs
- **FR-R1** HR creates a new hire (name, role, location, start date, manager + sensitive fields)
  and selects a published template → system compiles the DAG and starts a run.
- **FR-R2** Task states: `pending → ready → running → (waiting_approval) → done | failed | skipped | cancelled`.
- **FR-R3** Independent tasks run in parallel; a task becomes `ready` only when all dependencies
  are `done` or `skipped`.
- **FR-R4** Failed tasks retry with backoff; after max attempts the run is `blocked` and HR is
  notified.
- **FR-R5** HR can pause, resume, cancel a run, and retry or manually complete a task (with a
  reason, audited).
- **FR-R6** Runs survive process restarts (state checkpointed in MongoDB).

### 6.3 Human-in-the-loop & approvals
- **FR-H1** An `approval` task pauses its branch. The **Approvals Agent** prepares an approval
  packet (summary, risk flags, recommended decision) — using masked data only.
- **FR-H2** The approver is notified in the web **Approvals Inbox** and through the **mock
  Telegram** channel (message with Approve/Reject buttons rendered in the mock outbox UI).
  First decision wins.
- **FR-H3** Decision + comment + channel + user are stored and emitted as an event that resumes
  the run.
- **FR-H4** Approvals have an SLA; overdue approvals remind the approver, then escalate to HR.
- **FR-H5** Any tool call marked `requires_approval` in policy becomes an approval task
  automatically.

### 6.4 Orchestrator chat
- **FR-C1** HR/managers ask questions about a run; answers come from run state, task history and
  audit log through read-only tools, with links to referenced tasks. Managers only for their reports.
- **FR-C2** Chat cannot change run state except through explicit, confirmed actions.

### 6.5 Agent & capability registry
- **FR-A1** Registry stores each agent's manifest (§7.3). Versioned; changes are audited.
- **FR-A2** UI (HR_ADMIN) shows agents, their connector/model, allowed tools, and recent denials.

### 6.6 Notifications (mock)
- **FR-N1** Internal Notification tool sends via channel adapters. MVP channels: **in-app** and a
  **Telegram mock** that writes to `mock_outbox`. No real Telegram API calls in MVP.
- **FR-N2** The UI shows the mock outbox, including clickable Approve/Reject buttons that hit the
  same webhook endpoint a real Telegram callback would — so swapping in the real adapter later
  only changes the adapter.

### 6.7 Sensitive data handling (user-facing)
- **FR-P1** Sensitive fields display masked everywhere by default.
- **FR-P2** HR/HR_ADMIN can **reveal** a single field with a mandatory reason; the reveal is
  audited and expires after a short time in the UI.
- **FR-P3** Government document uploads (Aadhaar/PAN scans) are stored encrypted; agents see only
  metadata (`type`, `uploaded_at`, `verified`).
- **FR-P4** Only HR/HR_ADMIN can create or update a sensitive value, through the UI/API. Updates
  create a new vault version and a new token; the old one is kept (superseded) for audit.
- **FR-P5** Every reveal and every token resolution (detokenisation) is recorded in
  `pii_access_log` and visible to HR_ADMIN.
- **FR-P6** Sensitive values are purged **90 days after onboarding completes**; tokens then
  resolve to `[purged]`.

---

## 7. System architecture

```
┌─────────────────────────── apps/web (React) ───────────────────────────┐
│ Template Studio · Change requests · New hires · Run board · Approvals  │
│ inbox · Mock outbox · Chat · Trace links                               │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ REST + SSE (JWT: sub, org_id, role)
┌──────────────────────────────────▼──────────────────────────────────────┐
│ apps/api (FastAPI) — application layer / onboarding service             │
│ auth · tenant context · PII vault · templates · hires · runs ·          │
│ approvals · chat · webhooks (mock telegram callback)                    │
└───────┬─────────────────────────────────────────────────────────────────┘
        │ writes domain change + event in one transaction (outbox)
┌───────▼──────────────── MongoDB event bus ──────────────────────────────┐
│ events · jobs (leased queue) · dead_letters      (all carry org_id)     │
└───────┬─────────────────────────────────────────────────────────────────┘
        │ consumers (set tenant context from job)
┌───────▼──────────── apps/orchestrator (LangGraph worker) ───────────────┐
│ DAG compiler → LangGraph StateGraph · Mongo checkpointer · interrupt()  │
│ LLM connector registry (bedrock | langchain | fake)                     │
│ Agent Hub: resolves agent from registry, builds scoped toolset          │
│  ├─ Template Builder   ├─ Approvals Agent   ├─ DB Agent                 │
│  ├─ Templates Agent    ├─ Provisioning Agent (mock) ├─ Run Assistant    │
│ Guardrails · Tools hub (render, dates, notify)                          │
└───────┬─────────────────────────────────────────────────────────────────┘
        │ MCP (streamable HTTP) + short-lived agent token (agent, org, run)
┌───────▼──────────── apps/mcp-hub (gateway) ─────────────────────────────┐
│ verify token → policy (registry) → tenant injection → PII masking       │
│ → credential injection → audit → route                                  │
│  ├─ mcp-mongo (REAL — primary focus)                                    │
│  └─ mcp-mocks (telegram, bitbucket, provisioning — thin)                │
└─────────────────────────────────────────────────────────────────────────┘
 All services → OTel SDK (+ PII redaction processor) → otel-collector → Jaeger
```

### 7.1 Orchestrator & pluggable LLM connectors
- **Compilation:** published template version + variables → pruned DAG → LangGraph `StateGraph`
  (one node per task; edges from `depends_on`; parallel branches fan out/in).
- **Durability:** `langgraph-checkpoint-mongodb`; `thread_id = "{org_id}:{run_id}"`.
- **HIL:** approval nodes call `interrupt()`; the approval event resumes with `Command(resume=…)`.
- **Sub-agents:** each task node runs the registered agent as a subgraph with only its allowed
  tools, loaded from the MCP Hub via `langchain-mcp-adapters`.
- **Budgets:** per task max steps / tokens / timeout; per run total token budget.

**LLM connector layer (`libs/py-llm`)**
- `LLMConnector` protocol: `name`, `create_chat_model(model, params) -> BaseChatModel`,
  `healthcheck()`. Agents only ever receive a LangChain `BaseChatModel`, so LangGraph code is
  connector-agnostic.
- Connectors are discovered through a **registry** (Python entry-point group
  `onboarding.llm_connectors`), so a new connector is a new package, not a code change.
- Built-ins:
  - `bedrock` — `ChatBedrockConverse` (`langchain-aws`); credentials from the standard AWS
    credential chain via env (`AWS_REGION`, keys or profile). Supports Bedrock Guardrails config.
  - `langchain` — generic adapter over LangChain's `init_chat_model("provider:model")`, so any
    LangChain-supported provider can be used by configuration alone.
  - `fake` — deterministic scripted model for unit/integration tests and CI.
- **Model selection is configuration** (`config/llm.yaml` + env overrides):
  ```yaml
  default: { connector: bedrock, model: ${LLM_MODEL_STANDARD} }
  aliases:
    fast:     { connector: bedrock, model: ${LLM_MODEL_FAST} }
    standard: { connector: bedrock, model: ${LLM_MODEL_STANDARD} }
    deep:     { connector: bedrock, model: ${LLM_MODEL_DEEP} }
  ```
  Resolution order: agent manifest → org override → global default. Changing a model takes effect
  for **new** tasks; running tasks keep the model they started with (recorded on the task).
- Every LLM span records `llm.connector`, `llm.model`, token usage.
- No credentials or model IDs in code; startup fails fast if the selected connector's required
  env vars are missing.

### 7.2 Event bus on MongoDB
- `events`: append-only `{_id, org_id, type, payload, run_id, idempotency_key, traceparent, created_at}`.
- `jobs`: work queue; workers claim via `findOneAndUpdate` with a lease (`locked_until`),
  heartbeat, `attempts`, exponential backoff; exhausted jobs → `dead_letters`.
- Domain change + event written in one transaction (outbox; Mongo runs as a replica set).
- Behind an `EventBus` interface so Kafka/NATS can replace it later.

### 7.3 Agent Registry (manifest)
```yaml
name: db_agent
version: 1
purpose: Read and update onboarding records for a single run
llm: { alias: standard }              # or { connector: langchain, model: "..." }
mcp:
  mongo:
    tools: [find, aggregate, count, update_one]
    collections:
      read:  [new_hires, run_tasks, documents_meta]
      write: [run_tasks, documents_meta]
tools: [date_calc]
memory_scopes: [run_state:read, knowledge:hr_policies]
requires_approval: [update_one:new_hires]
limits: { max_steps: 8, max_tokens: 20000, timeout_s: 60 }
```
Sensitive values are not listed in manifests because **no manifest can grant access to them**:
tenant documents only contain PII tokens, and the vault is unreachable through MCP (§7.10).
PII resolution is granted per *template task* (`pii_access`), never per agent.

### 7.4 MCP Hub
- Single entry point for all MCP servers. Agents get a **short-lived signed token** per task
  (`agent`, `run_id`, `org_id`, `scopes`, `exp ≤ 15 min`).
- Pipeline per call: verify token → check tool against manifest → validate args against the tool
  contract (JSON schema) → **inject tenant scope from the token** → inject credentials → call →
  PII scan of the response → audit → span.
- `pii_vault`, `org_keys` and `pii_access_log` are hard-denied for every agent regardless of manifest.
- Every denial is an audit event and a span with `outcome=denied`.

### 7.5 MongoDB MCP server (real — primary integration)
Built in-house with FastMCP, in `apps/mcp-mongo`.

| Tool | Notes |
|---|---|
| `list_collections` | Only collections allowed for the calling agent |
| `find` | Filter + projection + sort; mandatory `limit` (≤ 100) |
| `count` | |
| `aggregate` | Allowlisted stages only: `$match $project $group $sort $limit $count $unwind $addFields` |
| `insert_one` / `update_one` | Only on the agent's write collections; `requires_approval` honoured |

- **No** delete, drop, index, admin or `runCommand` tools.
- Query validator rejects `$where`, `$function`, `$accumulator`, `$expr` with JS, `$lookup`,
  `$graphLookup`, `$out`, `$merge`, `$unionWith`, and any operator not on the allowlist.
- **Tenant scope comes from the token, never from arguments.** Any `org_id` in the agent's filter
  is rejected; the server prepends `{org_id: <token org>}` to every filter, sets it on every
  insert, adds `$match: {org_id}` as the first aggregation stage, and forbids `$set` of `org_id`.
- Tenant documents hold only PII **tokens**, so reads never expose values. Token fields
  (`pii.*`) are read-only for agents: any insert/update touching them is rejected.
- Defence in depth: separate MongoDB users with least-privilege roles (read-only vs read-write on
  named collections); the MCP server cannot exceed DB-level permissions even with a policy bug.
- Every call: audit record + OTel span (`db.operation`, `db.collection`, `agent.name`, `org_id`,
  result count — never documents).
- Tested end to end against a throw-away MongoDB container (single-node replica set via
  testcontainers / Docker), including the cross-tenant and injection test suites (§7.9).

### 7.6 Memory
| Store | Collection(s) | Who reads |
|---|---|---|
| Run state | `onboarding_runs`, `run_tasks`, LangGraph checkpoints | Orchestrator; agents get a slice for their task |
| Conversation | `conversations`, `messages` | Run Assistant only |
| Knowledge | `knowledge_docs` (HR policies, FAQs) — text search in MVP | Agents with `knowledge:*` scope |
| Audit | `audit_log` (append-only) | HR_ADMIN UI |

### 7.7 Observability
- OTel SDK in every Python service; auto-instrumentation for FastAPI, PyMongo, httpx;
  OpenInference LangChain instrumentation for LLM/agent/tool spans.
- `traceparent` stored on events/jobs so async continuations join the run's trace.
- Span attributes: `org_id`, `run_id`, `task_id`, `agent.name`, `tool.name`, `llm.connector`,
  `llm.model`, `guardrail.outcome`, token counts.
- A **PII redaction span processor** scrubs attributes/events with the same detectors as §7.10
  before export. Prompt/completion content capture is off by default.
- Local stack: otel-collector → Jaeger. The run page links to its trace.

### 7.8 Auth
- Simple JWT (HS256, secret from env): `sub`, `org_id`, `role`, `exp`. Password login with
  bcrypt. No refresh tokens in MVP.
- Managers only see runs, approvals and chat for hires where they are `manager_id`.

### 7.9 Tenant isolation

**Model:** shared database, `org_id` on every tenant-owned document. Designed so a
database-per-tenant strategy can be added later behind the same repository interface.

| Layer | Enforcement |
|---|---|
| Identity | `org_id` is a signed JWT claim; never accepted from request body/query/path. |
| Request context | FastAPI dependency sets a `TenantContext` (contextvar) per request; missing context → 401. |
| Data access | All DB access goes through a `TenantRepository` base: it **adds** `org_id` to every filter, sets it on every insert, prepends `$match` to every pipeline, and **raises** if called without a tenant context. Raw collection handles are not exported from the data lib. |
| Safety net | A PyMongo command listener (tests + staging) fails any command on a tenant collection whose filter lacks `org_id`. |
| Indexes | Every index on tenant collections starts with `org_id`; unique indexes include it (e.g. `{org_id, email_hash}`). |
| IDs | Lookups by id are always `{_id, org_id}` → a foreign id returns **404**, not 403 (no existence leak). |
| Event bus | Events/jobs carry `org_id`; consumers set `TenantContext` from the job before doing anything; jobs without `org_id` go to dead letters. |
| Orchestrator | LangGraph `thread_id = org_id:run_id`; checkpoints carry `org_id`; agent tokens are bound to one org and one run. |
| MCP Hub / Mongo MCP | Tenant scope injected from the agent token (§7.5); agent-supplied `org_id` rejected. |
| Encryption | Per-org data encryption key (§7.10), so one tenant's key cannot decrypt another's data. |
| Caches / keys | Any cache key or storage path is prefixed with `org_id`. |
| Telemetry | `org_id` on every span and audit record. |

**Testing:** a dedicated **cross-tenant test suite** seeds two orgs and, for every API endpoint,
every MCP tool and every event consumer, asserts that an actor from org A cannot read, list,
update or infer anything from org B. This suite runs in CI and blocks merges.

### 7.10 Sensitive data — tokenised PII vault

**Sensitive fields:** mobile number, email address, Aadhaar, PAN, bank details (account number,
IFSC, account-holder name), and government document scans (Aadhaar/PAN images/PDFs).

**Principle.** Raw values live in exactly one place: the **PII vault** (`libs/py-crypto`,
collection `pii_vault`). Every other place — `new_hires`, events, run state, prompts, agent
outputs, tool arguments, logs, traces — carries only an opaque **PII token** (`ptk_…`). Tokens
can be passed freely between agents; they are worthless without a grant.

```
HR (UI) ── PUT value ──▶ API ──▶ Vault: encrypt + sign ──▶ token stored on new_hires.pii.<field>
Orchestrator, at task start ── issues signed grant from template pii_access ──▶ tool context
                                                          (out-of-band, never in the prompt)
LLM sees / writes only:  "Hi Priya … "  recipient = ptk_01JD…
Sink (e.g. notify.telegram):
   verify grant → verify token binding → verify vault record signature
   → decrypt → substitute → send → store a masked copy only → log to pii_access_log
```

**Vault record**
```
{ token: "ptk_01JD…", org_id, subject: { type: "new_hire", id }, field: "mobile",
  ciphertext, nonce, key_version, version, status: active | superseded | purged,
  created_by, created_at, sig }
```
- **Tokens** are random and unguessable (ULID + 128 random bits); they encode nothing about the value.
- **Encryption:** AES-256-GCM with a per-org data key (envelope encryption; master key from env
  `PII_MASTER_KEY`). The AEAD *associated data* binds `org_id | subject.id | field | token | version`,
  so ciphertext copied into another record fails to decrypt.
- **Integrity:** `sig` = HMAC-SHA256 over every field except itself, using a per-org signing key
  separate from the encryption key. Any change made directly in the DB (e.g. re-pointing a token
  to another hire) is detected and the read **fails closed**.
- **Writers:** only humans via the API (`PUT /hires/{id}/pii/{field}`, HR/HR_ADMIN). An update
  creates a new version + new token and marks the old one `superseded`. No agent, MCP tool or event
  consumer has a write path to the vault.

**Signed grants — who may resolve what**
- A template task declares `pii_access: { fields, sink }`. At publish time this is checked against
  the **field → sink policy** (MVP: `mobile → notify.telegram`, `email → notify.email`; Aadhaar,
  PAN and bank fields → no agent sink at all).
- When the task starts, the orchestrator (not the LLM) issues a **grant**: a JWS signed with the
  orchestrator's key, `{org_id, run_id, task_id, subject_id, fields, sink, exp ≤ 10 min, jti}`.
  It travels in the tool-call context, out of band, so the model can't read, forge or widen it.
- The sink verifies: grant signature and expiry; sink id matches; every token in the payload
  belongs to the grant's `org_id` and `subject_id` (the run's hire) and a granted field; the vault
  record's signature is valid. **Any failure → nothing is sent**, the task fails, a guardrail event
  and audit record are written.
- Net effect: the AI can place a token in a message, but it cannot see the value, invent a token,
  swap in another hire's token, resolve a field the template didn't grant, or alter stored data.

| Concern | Rule |
|---|---|
| Lookup / dedupe | HMAC-SHA256 blind index (`email_hash`, `mobile_hash`, `pan_hash`) to search/deduplicate without decrypting. |
| Aadhaar | **Full 12-digit Aadhaar number is stored** — only in the vault, in a dedicated partition (`pii_vault_aadhaar`) with its own per-org key, following the UIDAI "Aadhaar Data Vault" model (encrypted store, reference tokens everywhere else). Validated on input (12 digits, not starting with 0/1, Verhoeff checksum). Blind index `aadhaar_hash` for dedupe. **Reveal: HR_ADMIN only**, with reason. No agent sink may ever resolve it. Consent recorded at capture; an alternative ID is accepted if the hire declines. MVP keeps the master key in env; production must move keys to a KMS/HSM. |
| Documents | Scans encrypted before writing to GridFS, bound to the hire like vault records; agents only see metadata. |
| Display | Masked by default: mobile `98XXXXXX21`, email `j***@domain.com`, Aadhaar `XXXX XXXX 1234`, PAN `ABXXXXX4F`, account `XXXXXX6789`. |
| Reveal | HR/HR_ADMIN only, one field at a time, mandatory reason, logged in `pii_access_log`, short-lived in UI. Managers never. |
| Free-text input | Chat messages and uploaded text pass through PII detectors before reaching an LLM: Aadhaar (12 digits + Verhoeff checksum), PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), IFSC (`[A-Z]{4}0[A-Z0-9]{6}`), Indian mobile (`(\+91)?[6-9]\d{9}`), email. Matches are replaced with a typed redaction marker (`[PAN]`) and are not persisted. Bedrock Guardrails PII filters add a second layer on the Bedrock connector. |
| MCP | Documents contain tokens only; `pii.*` fields are read-only for agents; vault collections are unreachable. |
| Logs & traces | Tokens are allowed; raw values are scrubbed by the redaction processor; PII endpoints never log bodies. |
| Retention | Vault values purged **90 days after onboarding completes** (ciphertext deleted, record kept as `purged`); a consent/purpose record kept per hire (DPDP Act 2023 principles — legal review still required). |

### 7.11 Deployment (local + VM)
- **Docker Compose** is the only deployment target for MVP: same compose file locally (`dev`
  profile, hot reload) and on a single VM (`vm` profile, built images).
- Services: `web` (static build behind the proxy), `api`, `orchestrator` (scalable with
  `--scale`), `mcp-hub`, `mcp-mongo`, `mcp-mocks`, `mongo` (replica set, auth enabled, keyfile,
  persistent volume), `otel-collector`, `jaeger`, `caddy` (reverse proxy + TLS).
- Only `caddy` exposes ports (80/443). Mongo and internal services stay on the private Docker
  network; Jaeger is reached via the proxy with basic auth or an SSH tunnel.
- Secrets live in a `.env` on the VM (never committed); `.env.example` documents every variable;
  each service validates required env at startup.
- Healthchecks + `restart: unless-stopped`; nightly `mongodump` backup to a mounted volume.
- `make seed` creates the org, an HR_ADMIN user, sample managers and seed templates.

---

## 8. Guardrails & evaluations

### 8.1 Guardrails

| Layer | Check | Mechanism |
|---|---|---|
| **Input** | Prompt injection / jailbreak in chat, uploads, external text | Delimit untrusted content in prompts; Bedrock Guardrails prompt-attack filter when on Bedrock; heuristic detector for other connectors |
| **Input** | PII to the LLM | Tokens only (§7.10); detectors redact free text |
| **Tool** | AI reading, inventing, swapping or altering PII | Signed grants + token binding + vault record integrity (§7.10) |
| **Output** | Structured output validity | Pydantic validation; one repair retry, then fail the task visibly |
| **Output** | Harmful/off-topic content, PII in generated messages | Output PII scan + Bedrock Guardrails content filters (when available) |
| **Tool** | Agent uses only allowed tools/collections | MCP Hub policy from registry |
| **Tool** | Dangerous or cross-tenant queries | Mongo MCP validator + tenant injection (§7.5, §7.9) |
| **Action** | Meaningful side effects | `requires_approval` → HIL approval task |
| **Budget** | Runaway loops/cost | Max steps/tokens/time per task; token budget per run |
| **Template** | Builder agent produces unsafe/invalid DAG or change request | Schema + graph + registry + PII-reference validation; publish needs HR_ADMIN |

Guardrails are implemented in `libs/py-guardrails` and are connector-agnostic; Bedrock Guardrails
is an optional extra layer, not a dependency. All decisions are audited and emitted as span events.

### 8.2 Agent evaluations (deterministic — no LLM-as-judge)

Every agent has an eval suite that is graded **by code only**. Agents return structured outputs
(`status: ok | refused | needs_human` plus typed fields like `risk_flags`, `recommendation`,
`citations`, `proposed_action`), which makes outcomes checkable without a judge model.

| Grader family | Examples |
|---|---|
| Contract | Output validates against the Pydantic schema; template DAG is acyclic and registry-valid |
| Outcome | Exact / set / substring matches on structured fields; expected task coverage in generated templates |
| Trajectory | Required tool calls made (with argument subsets), forbidden tools not called, step limits |
| State | Database state after the run (seeded throw-away Mongo) matches expectations; nothing else changed |
| Safety | No raw PII in output / tool args / LLM input / traces; PII tokens bound to the right hire and granted field; no cross-tenant data in MCP audit; injected instructions not followed |
| Cost | Steps, tokens, latency within budget |

- **Dataset:** `evals/datasets/onboarding_agents_v1.jsonl` (27 cases: Template Builder draft +
  suggest, Approvals, DB, Templates, Run Assistant; 13 capability, 14 safety), backed by
  `evals/fixtures/seed_v1.json` (two synthetic orgs). Format and check catalog in `evals/README.md`.
- **Runner:** `libs/py-evals` + `nx run evals:run --agent <name> --llm <alias|connector:model> --repeat 3`.
  Each case runs in a fresh fixture state; spans carry `eval.run_id` / `eval.case_id`.
- **Metrics:** pass@1, pass^k (all k repetitions pass — consistency), tool-call accuracy, steps,
  latency, tokens; per agent, per category, per model.
- **Gates:** safety cases 100% pass^3; capability ≥ 90% pass@1 and ≥ 80% pass^3; no regression
  > 5 points vs the last baseline.
- **When:** CI runs a harness self-test with the `fake` connector on every PR; the full suite
  against the real connector runs on demand and is required before merging changes to prompts,
  manifests, tools, guardrails or model aliases (this is also how a model swap is validated).
- Every agent bug found becomes a `regression` case.

---

## 9. Data model (MongoDB; all tenant collections carry `org_id`)

`orgs`, `org_keys` (wrapped data + signing keys), `users`, `new_hires` (PII as tokens + blind
indexes), `pii_vault`, `documents` (GridFS, encrypted) + `documents_meta`, `templates`, `template_versions`,
`template_drafts`, `template_change_requests`, `onboarding_runs`, `run_tasks`, `approvals`,
`events`, `jobs`, `dead_letters`, `agent_registry`, `tool_catalog`, `conversations`, `messages`,
`knowledge_docs`, `audit_log`, `pii_access_log` (reveals + detokenisations), `mock_outbox`,
`eval_runs`, LangGraph checkpoint collections.

## 10. API surface (high level)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `GET /me` |
| Templates | `POST /templates/drafts/chat` (SSE), `GET/PUT /templates/drafts/{id}`, `POST /templates/drafts/{id}/validate`, `POST /templates/drafts/{id}/publish`, `GET /templates` |
| Change requests | `POST /templates/{key}/change-requests/chat` (SSE, manager), `GET /change-requests` (HR queue; managers see their own), `POST /change-requests/{id}/{accept\|reject}` |
| Hires & runs | `POST /hires`, `PUT /hires/{id}/pii/{field}` (HR only), `POST /hires/{id}/pii/{field}/reveal`, `POST /hires/{id}/documents`, `POST /runs`, `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/{pause\|resume\|cancel}`, `POST /runs/{id}/tasks/{task_id}/{retry\|complete}` |
| Approvals | `GET /approvals?mine=true`, `POST /approvals/{id}/decision` |
| Chat | `POST /runs/{id}/chat` (SSE) |
| Registry & audit | `GET /agents`, `GET /agents/{name}`, `GET /audit`, `GET /pii-access-log` (HR_ADMIN) |
| Evals | `GET /evals/runs`, `GET /evals/runs/{id}` (HR_ADMIN, read-only) |
| Mock channels | `GET /mock-outbox`, `POST /webhooks/telegram` (same contract as the real callback) |
| Live updates | `GET /runs/{id}/stream` (SSE) |

All responses use one envelope: `{ success, data, error, meta }`. `org_id` never appears as an input.

## 11. Tech stack

| Concern | Choice |
|---|---|
| Monorepo | Nx, `@nxlv/python` + uv for Python projects |
| Backend | Python 3.12, FastAPI, Pydantic v2, PyMongo (async) |
| Agents | LangGraph, `langchain-mcp-adapters`, `langgraph-checkpoint-mongodb` |
| LLM | `libs/py-llm` connector registry: `bedrock` (`langchain-aws`), `langchain` (`init_chat_model`), `fake` |
| MCP | FastMCP (Mongo server + hub), streamable HTTP |
| Crypto | `cryptography` (AES-GCM, HMAC) |
| Frontend | React + Vite + TypeScript, TanStack Query, React Flow, shadcn/ui |
| Contracts | Pydantic → OpenAPI → generated TS client |
| DB / cache / bus | MongoDB 7+ (replica set) |
| Observability | OpenTelemetry SDK, OpenInference instrumentation, otel-collector, Jaeger |
| Deployment | Docker Compose (local + single VM), Caddy |
| Tests | pytest + testcontainers (Mongo), Vitest, Playwright |
| Evals | In-house deterministic harness (`libs/py-evals`), JSONL datasets |

**Repo layout**
```
apps/   web · api · orchestrator · mcp-hub · mcp-mongo · mcp-mocks
libs/   py-contracts · py-data (tenant repos) · py-crypto (PII vault) · py-llm · py-agents
        py-guardrails · py-telemetry · py-eventbus · py-evals · ts-ui · ts-api-client
config/ llm.yaml · agents/*.yaml (registry seeds) · pii_sink_policy.yaml
templates/  seed templates (YAML)
evals/      datasets (JSONL) · fixtures · reports (generated)
infra/      docker-compose.yml, Caddyfile, collector config, mongo init/keyfile scripts
docs/       PRD, architecture, ADRs
```

## 12. Non-functional requirements

| Area | Target (MVP) |
|---|---|
| Tenant isolation | Cross-tenant suite passes 100% for all endpoints, MCP tools, consumers; runs in CI |
| PII | 0 raw sensitive values outside the vault — prompts, outputs, logs, traces (verified by automated scans and the eval `no_raw_pii` check) |
| PII integrity | 100% of tampered vault records / forged or widened grants rejected (dedicated tests) |
| Agent quality | Eval gates in §8.2 met before any agent/prompt/model change merges |
| Durability | No run state lost on worker restart; jobs at-least-once with idempotent handlers |
| Latency | API p95 < 300 ms (non-LLM); approval decision → run resumed < 5 s |
| Security | No secrets in code; agent tokens ≤ 15 min; inputs validated at boundaries |
| Observability | 100% of LLM and tool calls traced; every run has a trace link |
| Portability | Switching an agent's model/connector requires config only |
| Quality | ≥ 80% coverage on Python libs; E2E for the golden path |

## 13. Success metrics

- Golden path demo on the VM: template drafted by the agent → manager suggestion accepted →
  published → hire added → run completes with one approval via the Telegram mock → full trace in
  Jaeger with no PII in it.
- ≥ 70% of tasks in the seed template complete without human action (excluding approvals).
- 0 successful cross-agent or cross-tenant access; 100% of attempts logged.
- Model swap demo: change `standard` alias in config, next task uses the new model.

## 14. Milestones

| # | Milestone | Outcome |
|---|---|---|
| M0 | Foundations | Nx workspace, Docker Compose (Mongo rs + auth, collector, Jaeger, Caddy), CI, telemetry lib, `.env.example` |
| M1 | Core platform | Contracts, JWT + roles, **tenant context + TenantRepository + cross-tenant suite**, **PII vault** (tokens, encryption, record signatures, grants, masking, blind index, reveal) |
| M2 | Mongo MCP | **Real Mongo MCP** with validator, tenant injection, masking, least-privilege DB users; MCP Hub with registry policy & audit; thin mocks |
| M3 | LLM + templates + evals | `py-llm` connector registry (bedrock/langchain/fake), template schema + validator, Template Builder agent (draft + suggest mode), DAG compiler, **eval harness + seed dataset wired in** |
| M4 | Runtime | Event bus, LangGraph orchestrator, sub-agents, guardrails, HIL approvals, mock Telegram flow |
| M5 | Frontend | Studio, change requests, run board, approvals inbox, mock outbox, chat, PII reveal, trace links |
| M6 | Hardening | Expand eval datasets + baselines, PII leak/tamper tests, E2E, VM deployment runbook + backups |

## 15. Risks

| Risk | Mitigation |
|---|---|
| Cross-tenant leak via a forgotten filter | Repository-only data access, command-listener safety net, cross-tenant CI suite |
| PII exposure to LLM/traces/logs | Tokens everywhere outside the vault, detectors, redaction processors, automated leak scan |
| AI manipulating PII (swap, forge, widen) | Human-only vault writes, AEAD binding, record HMAC, orchestrator-signed grants verified at the sink |
| Brittle evals (free-text matching) | Structured agent outputs; free-text checks only as `contains_any` with alternatives; pass^k to spot flakiness |
| Aadhaar storage compliance (full number stored) | Dedicated Aadhaar vault partition + separate key, HR_ADMIN-only reveal, consent + alternative ID, KMS/HSM before production, legal review of UIDAI / DPDP obligations |
| Key loss / leakage | Envelope encryption, key versioning, master key only in VM env; documented rotation |
| LLM output inconsistent | Template-first; structured outputs; repair-once then fail visibly |
| Mongo as event bus limits | Interface abstraction; leases + idempotency |
| Connector feature gaps (e.g. guardrails only on Bedrock) | Core guardrails in our own lib; provider guardrails are additive |
| Single VM = single point of failure | Acceptable for MVP; backups + documented restore |

## 16. Decisions log & open questions

**Decided (v0.2):** Bedrock creds via env; pluggable LLM connectors · Telegram mocked, Mongo MCP is
the only real integration · Sensitive = mobile, email, Aadhaar, PAN, bank details, gov doc scans ·
Deployment = Docker Compose on a VM · Managers can suggest template changes · Strict tenant isolation.

**Decided (v0.3):** Tokenised PII vault with signed grants and integrity-checked records · PII
retention 90 days after completion · Manager suggestions in a separate HR queue · Deterministic
evals (no LLM-as-judge) with seed dataset · VM sizing out of scope for now · **Full Aadhaar number
stored** in a dedicated vault partition (HR_ADMIN-only reveal).

**Still open:** none blocking. Legal review of Aadhaar/DPDP obligations before production.
