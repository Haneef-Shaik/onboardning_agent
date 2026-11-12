/** The run board's calls, all through the generated client (PRD §11). */
import { unwrap, type ApiClient, type RunRecord, type RunStatus, type RunSummary } from "@onb/ts-api-client";

export async function listRuns(client: ApiClient, status?: RunStatus): Promise<RunSummary[]> {
  return unwrap(await client.GET("/runs", { params: { query: status === undefined ? {} : { status } } }));
}

export async function getRun(client: ApiClient, runId: string): Promise<RunRecord> {
  return unwrap(await client.GET("/runs/{run_id}", { params: { path: { run_id: runId } } }));
}

export async function pauseRun(client: ApiClient, runId: string): Promise<RunRecord> {
  return unwrap(await client.POST("/runs/{run_id}/pause", { params: { path: { run_id: runId } } }));
}

export async function resumeRun(client: ApiClient, runId: string): Promise<RunRecord> {
  return unwrap(await client.POST("/runs/{run_id}/resume", { params: { path: { run_id: runId } } }));
}

export async function cancelRun(client: ApiClient, runId: string): Promise<RunRecord> {
  return unwrap(await client.POST("/runs/{run_id}/cancel", { params: { path: { run_id: runId } } }));
}

export async function retryTask(client: ApiClient, runId: string, taskId: string): Promise<RunRecord> {
  return unwrap(
    await client.POST("/runs/{run_id}/tasks/{task_id}/retry", { params: { path: { run_id: runId, task_id: taskId } } }),
  );
}

/** A reason is mandatory (FR-R5); it is audited, so it must not carry personal data. */
export async function completeTask(
  client: ApiClient,
  runId: string,
  taskId: string,
  reason: string,
): Promise<RunRecord> {
  return unwrap(
    await client.POST("/runs/{run_id}/tasks/{task_id}/complete", {
      params: { path: { run_id: runId, task_id: taskId } },
      body: { reason },
    }),
  );
}
