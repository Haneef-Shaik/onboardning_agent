import type { Role, RunRecord, RunTaskRecord } from "@onb/ts-api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { createAppClient } from "../api";
import { createSessionStore } from "../auth/session";
import { RunDetail } from "./RunDetail";

afterEach(cleanup);

const TRACE = "4bf92f3577b34da6a3ce929d0e0e4736";

function task(id: string, status: RunTaskRecord["status"], extra: Partial<RunTaskRecord> = {}): RunTaskRecord {
  return {
    task_id: id,
    title: `Task ${id}`,
    type: "action",
    agent: "a",
    action: "x",
    depends_on: [],
    status,
    skip_reason: null,
    due: null,
    attempts: 0,
    error: null,
    ...extra,
  };
}

function blockedRun(): RunRecord {
  return {
    id: "run_1",
    hire_id: "h_1",
    template_key: "engineer-india",
    template_version: 1,
    dag_hash: "d",
    status: "blocked",
    created_by: "u_acme_hr1",
    created_at: "2026-11-12T09:00:00Z",
    trace_id: TRACE,
    tasks: [task("a", "done"), task("b", "failed", { attempts: 3, error: "sink_rejected" }), task("c", "pending")],
  };
}

function envelope(status: number, data: unknown, error: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "t", page: null } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** An API whose stream never says anything (the stream is proved in the hook's and Playwright's tests). */
function fakeApi(run: RunRecord) {
  const calls: { path: string; body: unknown }[] = [];
  const handler = async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname.replace(/^\/api/, "");
    if (path === "/runs/run_1/stream") {
      return new Response(new ReadableStream({ start() {} }), { headers: { "content-type": "text/event-stream" } });
    }
    if (path === "/runs/run_1" && request.method === "GET") return envelope(200, run);
    const control = /^\/runs\/run_1\/(pause|resume|cancel|tasks\/[a-z_0-9]+\/(retry|complete))$/.exec(path);
    if (control && request.method === "POST") {
      const text = await request.text();
      calls.push({ path, body: text === "" ? null : JSON.parse(text) });
      return envelope(200, { ...run, status: "running" });
    }
    return envelope(404, null, { code: "NOT_FOUND", message: "Not found", details: [] });
  };
  return { handler, calls };
}

function renderDetail(role: Role, run: RunRecord = blockedRun()) {
  const api = fakeApi(run);
  const client = createAppClient({
    session: createSessionStore(),
    onUnauthenticated: () => undefined,
    baseUrl: "http://web.test/api",
    fetch: api.handler,
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RunDetail client={client} role={role} runId="run_1" jaegerBase="https://jaeger.test" retryDelayMs={() => 10} />
    </QueryClientProvider>,
  );
  return api;
}

describe("RunDetail: a blocked task and its reason (U4)", () => {
  it("shows the run as blocked and, on the failed task only, why", async () => {
    renderDetail("HR");
    expect(await screen.findByTestId("run-status")).toHaveAttribute("data-status", "blocked");
    const reasons = screen.getAllByTestId("blocked-reason");
    expect(reasons).toHaveLength(1);
    expect(within(screen.getByTestId("task-b")).getByTestId("blocked-reason")).toHaveTextContent(/rejected/i);
    expect(screen.getByTestId("task-a")).not.toHaveTextContent(/rejected/i);
  });

  it("retries the failed task", async () => {
    const api = renderDetail("HR");
    await userEvent.click(await within(await screen.findByTestId("task-b")).findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.calls).toEqual([{ path: "/runs/run_1/tasks/b/retry", body: null }]));
  });

  it("completes the failed task only with a reason", async () => {
    const api = renderDetail("HR_ADMIN");
    const row = await screen.findByTestId("task-b");
    await userEvent.click(within(row).getByRole("button", { name: "Complete…" }));
    const confirm = within(row).getByRole("button", { name: "Confirm complete" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(row).getByLabelText(/Reason for completing/), "   ");
    expect(confirm).toBeDisabled();
    await userEvent.type(within(row).getByLabelText(/Reason for completing/), "done by hand in the vendor portal");
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(api.calls).toEqual([
        { path: "/runs/run_1/tasks/b/complete", body: { reason: "done by hand in the vendor portal" } },
      ]),
    );
  });
});

describe("RunDetail: run controls", () => {
  it("offers only what the state allows", async () => {
    renderDetail("HR");
    const group = await screen.findByRole("group", { name: "Run controls" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel run"]);
  });

  it("pauses a running run", async () => {
    const api = renderDetail("HR", { ...blockedRun(), status: "running", tasks: [task("a", "running")] });
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() => expect(api.calls).toEqual([{ path: "/runs/run_1/pause", body: null }]));
  });
});

describe("RunDetail: the trace link", () => {
  it("opens Jaeger on the run's trace id for HR", async () => {
    renderDetail("HR");
    const link = await screen.findByRole("link", { name: "Open trace" });
    expect(link).toHaveAttribute("href", `https://jaeger.test/trace/${TRACE}`);
  });

  it("has no link for a run without a trace id", async () => {
    renderDetail("HR", { ...blockedRun(), trace_id: null });
    await screen.findByTestId("run-status");
    expect(screen.queryByRole("link", { name: "Open trace" })).toBeNull();
  });
});

describe("RunDetail: a manager", () => {
  it("sees the run and its blocked reason but has no controls, no task actions and no trace link", async () => {
    renderDetail("MANAGER");
    expect(await screen.findByTestId("blocked-reason")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Run controls" })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link", { name: "Open trace" })).toBeNull();
  });
});
