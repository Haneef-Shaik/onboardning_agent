/** The Approvals section's calls, all through the generated client (PRD §11; UI-6). */
import { ApiError, unwrap, type ApiClient, type components } from "@onb/ts-api-client";

export type ApprovalRecord = components["schemas"]["ApprovalRecord"];
export type MockOutboxMessage = components["schemas"]["MockOutboxMessage"];
export type Decision = components["schemas"]["Decision"];

/** The approvals the signed-in user may decide (theirs, or their role's). */
export async function listMyApprovals(client: ApiClient): Promise<ApprovalRecord[]> {
  return unwrap(await client.GET("/approvals", { params: { query: { mine: true } } }));
}

/** One decision; the first decision wins, a later one is 409 (`isAlreadyDecided`). */
export async function decideApproval(
  client: ApiClient,
  approvalId: string,
  decision: Decision,
  comment: string,
): Promise<ApprovalRecord> {
  const trimmed = comment.trim();
  return unwrap(
    await client.POST("/approvals/{approval_id}/decision", {
      params: { path: { approval_id: approvalId } },
      body: trimmed === "" ? { decision } : { decision, comment: trimmed },
    }),
  );
}

export async function listOutbox(client: ApiClient): Promise<MockOutboxMessage[]> {
  return unwrap(await client.GET("/mock-outbox", { params: { query: {} } }));
}

/**
 * A press on a mock-outbox button. The API checks the message is addressed to the caller and then calls
 * `POST /webhooks/telegram` itself, like a real Telegram callback: the webhook secret never reaches the browser.
 */
export async function pressOutboxButton(client: ApiClient, messageId: string, callbackData: string): Promise<void> {
  unwrap(
    await client.POST("/mock-outbox/{message_id}/press", {
      params: { path: { message_id: messageId } },
      body: { callback_data: callbackData },
    }),
  );
}

/** The API's answer to a decision that lost to an earlier one (first decision wins, FR-H2). */
export function isAlreadyDecided(error: unknown): boolean {
  return error instanceof ApiError && error.code === "CONFLICT";
}

export const ALREADY_DECIDED = "Already decided: another decision reached this approval first.";
