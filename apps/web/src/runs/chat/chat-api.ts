/** The run assistant's calls (ADR 0024), all through the generated client (PRD §11). */
import { ApiError, unwrap, type ApiClient, type ChatMessageRecord, type RunRecord } from "@onb/ts-api-client";
import { readSse } from "../sse";

export async function getChat(client: ApiClient, runId: string): Promise<ChatMessageRecord[]> {
  return unwrap(await client.GET("/runs/{run_id}/chat", { params: { path: { run_id: runId } } })) ?? [];
}

function isMessage(value: unknown): value is { message: ChatMessageRecord } {
  return typeof value === "object" && value !== null && "message" in value;
}

/** Asks one question. The answer arrives as one `message` event; a failure is an ordinary error envelope. */
export async function askRun(client: ApiClient, runId: string, message: string): Promise<ChatMessageRecord> {
  const { response, data, error } = await client.POST("/runs/{run_id}/chat", {
    params: { path: { run_id: runId } },
    body: { message },
    parseAs: "stream",
  });
  if (!response.ok || !data) {
    // An error answer is a normal envelope (the answer is computed before the stream starts).
    unwrap({ response, error: error ?? null });
    throw new ApiError("SERVICE_UNAVAILABLE", "The assistant did not answer; try again", response.status, null, []);
  }
  for await (const event of readSse(data)) {
    if (event.event === "message" && isMessage(event.data)) return event.data.message;
  }
  throw new ApiError("SERVICE_UNAVAILABLE", "The assistant did not answer; try again", 503, null, []);
}

/** Runs the retry the assistant proposed, once. Only HR and HR_ADMIN may; the API checks again. */
export async function confirmProposal(client: ApiClient, runId: string, proposalId: string): Promise<RunRecord> {
  return unwrap(
    await client.POST("/runs/{run_id}/chat/confirm", {
      params: { path: { run_id: runId } },
      body: { proposal_id: proposalId },
    }),
  );
}
