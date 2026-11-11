/**
 * The admin views' reads (UI-8; PRD FR-A2, U8, §10 Registry & audit, Evals). Every call goes through the
 * app's `ApiClient`, so the bearer token and the session-ending 401 handling apply. All read-only.
 */
import { unwrap, type ApiClient, type components, type PageMeta } from "@onb/ts-api-client";

type Schemas = components["schemas"];
export type AgentRecord = Schemas["AgentRecord"];
export type AuditRecord = Schemas["AuditRecord"];
export type AuditOutcome = Schemas["AuditOutcome"];
export type PiiAccessRecord = Schemas["PiiAccessRecord"];
export type EvalRunSummary = Schemas["EvalRunSummary"];
export type EvalRun = Schemas["EvalRun"];

/** One page of a list route: its items and the page meta (total, page, limit). */
export interface Paged<T> {
  readonly items: T[];
  readonly page: PageMeta | null;
}

interface PagedResult<T> {
  data?: { success: boolean; data: T[] | null; meta?: { page?: PageMeta | null } | null };
  error?: unknown;
  response: Response;
}

function paged<T>(result: PagedResult<T>): Paged<T> {
  const items = unwrap(result);
  return { items, page: result.data?.meta?.page ?? null };
}

/** How many denials an agent's page lists (the newest first). */
export const RECENT_DENIALS = 10;
/** The audit action and outcome the MCP hub records for a refused tool call (HUB-1, ADR 0006). */
export const DENIAL_FILTER = { action: "mcp.tool_call", outcome: "denied" } as const;

export async function listAgents(client: ApiClient): Promise<AgentRecord[]> {
  return unwrap(await client.GET("/agents"));
}

export async function getAgent(client: ApiClient, name: string): Promise<AgentRecord> {
  return unwrap(await client.GET("/agents/{name}", { params: { path: { name } } }));
}

/** The agent's most recent denied tool calls, from the audit log (FR-A2). */
export async function listRecentDenials(client: ApiClient, agent: string): Promise<Paged<AuditRecord>> {
  const query = { ...DENIAL_FILTER, actor_type: "agent" as const, actor_id: agent, limit: RECENT_DENIALS };
  return paged(await client.GET("/audit", { params: { query } }));
}

export interface AuditFilters {
  readonly action?: string | undefined;
  readonly outcome?: AuditOutcome | undefined;
  readonly actorId?: string | undefined;
}

export async function listAudit(
  client: ApiClient,
  filters: AuditFilters,
  page: number,
  limit: number,
): Promise<Paged<AuditRecord>> {
  const query = {
    page,
    limit,
    ...(filters.action ? { action: filters.action } : {}),
    ...(filters.outcome ? { outcome: filters.outcome } : {}),
    ...(filters.actorId ? { actor_id: filters.actorId } : {}),
  };
  return paged(await client.GET("/audit", { params: { query } }));
}

export async function listPiiAccess(client: ApiClient, page: number, limit: number): Promise<Paged<PiiAccessRecord>> {
  return paged(await client.GET("/pii-access-log", { params: { query: { page, limit } } }));
}

export async function listEvalRuns(client: ApiClient, page: number, limit: number): Promise<Paged<EvalRunSummary>> {
  return paged(await client.GET("/evals/runs", { params: { query: { page, limit } } }));
}

export async function getEvalRun(client: ApiClient, runId: string): Promise<EvalRun> {
  return unwrap(await client.GET("/evals/runs/{run_id}", { params: { path: { run_id: runId } } }));
}
