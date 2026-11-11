/**
 * Test-only: a stand-in for the API's login, `/me` and the admin read routes, with synthetic records.
 * It records every request, so tests can check which queries the views sent.
 */
import type { Role } from "@onb/ts-api-client";

export const PASSWORD = "test-only-password";

const USERS: Record<string, { user_id: string; name: string; role: Role }> = {
  "u_acme_hradmin@example.com": { user_id: "u_acme_hradmin", name: "Kavya Rao", role: "HR_ADMIN" },
  "u_acme_hr1@example.com": { user_id: "u_acme_hr1", name: "Sameer Khan", role: "HR" },
  "u_acme_mgr_ravi@example.com": { user_id: "u_acme_mgr_ravi", name: "Ravi Kumar", role: "MANAGER" },
};

const PAGE = (total: number, page = 1, limit = 50) => ({ total, page, limit });

export const DB_AGENT = {
  name: "db_agent",
  version: 2,
  active: true,
  purpose: "Read and update onboarding records for a single run",
  llm: { alias: "standard", connector: null, model: null, params: {} },
  mcp: {
    mongo: {
      tools: ["find", "aggregate", "count", "update_one"],
      collections: { read: ["new_hires", "run_tasks"], write: ["run_tasks"] },
    },
  },
  tools: ["date_calc"],
  memory_scopes: ["run_state:read"],
  requires_approval: [],
  limits: { max_steps: 8, max_tokens: 20000, timeout_s: 60 },
  sha256: "a".repeat(64),
  updated_at: "2026-11-01T08:00:00Z",
  resolved_llm: { connector: "fake", model: "fake-standard-model", alias: "standard", source: "manifest" },
};

const audit = (id: string, actor: [string, string], action: string, target: string, outcome: string, reason: string | null) => ({
  id,
  org_id: "org_acme",
  actor: { type: actor[0], id: actor[1] },
  action,
  target,
  outcome,
  reason,
  trace_id: null,
  at: "2026-11-01T08:05:00Z",
});

export const DENIAL = audit("a1", ["agent", "db_agent"], "mcp.tool_call", "tool:mongo.find/other", "denied", "collection_not_in_manifest");
const REVEAL = audit("a2", ["user", "u_acme_hr1"], "pii.reveal", "hire:h_acme_001/pan", "success", "payroll check");

export const ACCESS = {
  id: "p1",
  kind: "reveal",
  actor: { type: "user", id: "u_acme_hr1" },
  hire_id: "h_acme_001",
  field: "pan",
  token: "ptk_test_never_shown",
  masked: "XXXXXX123X",
  reason: "payroll check",
  sink: null,
  run_id: null,
  task_id: null,
  at: "2026-11-01T08:06:00Z",
};

export const RUN = {
  id: "evr_20261102T090000Z_0000aaa1",
  mode: "full",
  agent: "db_agent",
  llm: "standard",
  dataset: "onboarding_agents_v1.jsonl",
  fixture: "seed_v1",
  repeat: 3,
  passed: false,
  all_cases_passed: false,
  started_at: "2026-11-02T09:00:00Z",
  finished_at: "2026-11-02T09:04:00Z",
};

const RUN_DETAIL = {
  ...RUN,
  metrics: {
    db_agent: {
      all: {
        cases: 2,
        repetitions: 6,
        pass_at_1: 0.8333,
        pass_hat_k: 0.5,
        tool_call_accuracy: 1,
        avg_steps: 3.5,
        p50_latency_ms: 12,
        p95_latency_ms: 30.5,
        avg_tokens: 410,
      },
    },
  },
  gates: [{ name: "pass_hat_k", agent: "db_agent", passed: false, detail: "50.0% < 80.0%" }],
  cases: [{ case_id: "db-002", agent: "db_agent", category: "happy", passes: 2, repetitions: 3 }],
};

function envelope(status: number, data: unknown, error: unknown = null, page: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "trace-1", page } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const fail = (status: number, code: string, message: string) => envelope(status, null, { code, message, details: [] });

export function fakeAdminApi() {
  const requests: URL[] = [];
  const state = { forbidAdmin: false };
  const handler = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const path = url.pathname;
    requests.push(url);
    if (path === "/api/auth/login") {
      const { email, password } = (await request.json()) as { email: string; password: string };
      if (!(email in USERS) || password !== PASSWORD) return fail(401, "UNAUTHENTICATED", "Authentication required");
      return envelope(200, { access_token: `tok:${email}`, token_type: "bearer", expires_at: Date.now() / 1000 + 3600 });
    }
    const email = request.headers.get("authorization")?.replace("Bearer tok:", "") ?? "";
    const user = USERS[email];
    if (!user) return fail(401, "UNAUTHENTICATED", "Authentication required");
    if (path === "/api/me") return envelope(200, { ...user, org_id: "org_acme", email });
    if (state.forbidAdmin || user.role !== "HR_ADMIN") return fail(403, "FORBIDDEN", "Not allowed");
    if (path === "/api/agents") return envelope(200, [DB_AGENT]);
    if (path === "/api/agents/db_agent") return envelope(200, DB_AGENT);
    if (path === "/api/audit") {
      const denials = url.searchParams.get("outcome") === "denied";
      const items = denials ? [DENIAL] : [DENIAL, REVEAL];
      return envelope(200, items, null, PAGE(denials ? 1 : 120));
    }
    if (path === "/api/pii-access-log") return envelope(200, [ACCESS], null, PAGE(1));
    if (path === "/api/evals/runs") return envelope(200, [RUN], null, PAGE(1, 1, 20));
    if (path === `/api/evals/runs/${RUN.id}`) return envelope(200, RUN_DETAIL);
    return fail(404, "NOT_FOUND", "Not found");
  };
  return { handler, requests, state };
}
