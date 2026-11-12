# 0024 · How the Run Assistant chat stays read-only and how a retry gets confirmed

- **Status:** accepted · 2026-11-12 · goal CHAT-1
- **Context:** FR-C1 lets HR and managers ask about a run (managers only for their reports); FR-C2 says chat
  changes a run only through explicit, confirmed actions. The assistant is an agent (AG-1) with read-only
  Mongo tools. Questions: who decides what the assistant may be asked, what makes an action confirmed, and
  where chat text with personal data goes.

## Decision

1. **The API authorizes before the agent runs.** `POST /runs/{id}/chat` looks the run up with the rule
   `GET /runs/{id}` uses (`RunControl.visible_run`). A run the caller may not see (another org's, a hire
   another manager manages) is 404, before the assistant is called and before anything is stored. The agent
   is not trusted to enforce manager scope; its manifest also tells it to refuse (eval case `ra-003`).
2. **A proposal is API state, not agent output.** The agent may return `proposed_action`; the service accepts
   it only if it is a `retry_task` for a *failed* task of a *blocked* run, the run is the chat's, and the
   asker is HR/HR_ADMIN. It is then stored in `chat_proposals` (outside the agent's read grant) as `pending`
   with an id and a 15-minute expiry. Anything else is dropped from the reply.
3. **Confirm is a separate call.** `POST /runs/{id}/chat/confirm {proposal_id}` (HR/HR_ADMIN) runs the retry
   through `RunControl.retry_task`, once: the proposal must belong to the caller and the run, be pending and
   unexpired (compare-and-set `pending → confirmed`); another user's or unknown proposal is 404, a used or
   expired one 409. Words in chat ("yes, do it") never confirm anything. The retry is audited like any retry.
4. **Stored chat is redacted.** Question and answer pass through `onb_guardrails.pii.redact` before they are
   stored or (the question) sent to the agent; stored messages hold markers like `[PAN]`. Citations are kept
   only for tasks that exist in the chat's run.
5. **The chat is served only when the hub is configured** (`AGENT_TOKEN_KEY`, `MCP_HUB_URL`); otherwise its
   routes answer 503 and the rest of the API runs.

## Consequences

- The real-connector gate for `ra-001`…`ra-007` needs OW-1 (Bedrock): the adapter is registered
  (`onb_evals.agents.run_assistant`) and the fake-connector path is proven; the paid run is the remaining step.
- Proposals expire unconfirmed; they are not cleaned up (low volume; a sweep belongs with retention goals).
- Only `retry_task` is proposable; pause/cancel/complete stay on the run page.
