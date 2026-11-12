/** What the run board offers a role and a run state. The API checks every call again (FR-R5, ADR 0018). */
import type { Role, RunStatus } from "@onb/ts-api-client";

export type RunControl = "pause" | "resume" | "cancel";

const STAFF: readonly Role[] = ["HR_ADMIN", "HR"];

export function canControlRuns(role: Role): boolean {
  return STAFF.includes(role);
}

/** Managers see their reports' runs but have no trace (PRD U7 is an HR view). */
export function canOpenTrace(role: Role): boolean {
  return STAFF.includes(role);
}

export function controlsFor(status: RunStatus): RunControl[] {
  switch (status) {
    case "started":
    case "running":
      return ["pause", "cancel"];
    case "paused":
      return ["resume", "cancel"];
    case "blocked":
      return ["cancel"]; // a blocked run moves on when its failed tasks are retried or completed
    default:
      return [];
  }
}

const TRACE_ID = /^[0-9a-f]{32}$/;

/** Jaeger's page for one trace; null unless the id is a well-formed W3C trace id. */
export function traceUrl(jaegerBase: string, traceId: string | null | undefined): string | null {
  if (traceId === null || traceId === undefined || !TRACE_ID.test(traceId)) return null;
  return `${jaegerBase.replace(/\/+$/, "")}/trace/${traceId}`;
}

const CAUSES: Readonly<Record<string, string>> = {
  sink_rejected: "The system receiving the result rejected it",
  LeaseExpired: "The worker running it stopped responding",
  no_agent: "No agent is registered for this task",
  retired_agent: "The agent for this task has been retired",
  grant_refused: "The agent was refused a tool it needs",
  budget_exceeded: "The run's budget was used up",
  guardrail_blocked: "A guardrail blocked the agent's output",
};

/** The reason a task failed, from the cause code the API keeps (never a message). */
export function whyBlocked(error: string | null | undefined): string {
  if (!error) return "Failed, no cause recorded";
  return CAUSES[error] ?? `Failed: ${error}`;
}
