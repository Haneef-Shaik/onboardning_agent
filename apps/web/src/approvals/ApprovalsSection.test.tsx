import type { components, Me } from "@onb/ts-api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { createAppClient } from "../api";
import { createSessionStore } from "../auth/session";
import { ApprovalsSection } from "./ApprovalsSection";

type ApprovalRecord = components["schemas"]["ApprovalRecord"];
type MockOutboxMessage = components["schemas"]["MockOutboxMessage"];

afterEach(cleanup);

const RAVI: Me = { user_id: "u_acme_mgr_ravi", org_id: "org_acme", role: "MANAGER", name: "Ravi", email: "r@example.com" };
const BUTTON_APPROVE = "ob1:AAAAAAAAAAAAAAAAAAAAAA";
const BUTTON_REJECT = "ob1:BBBBBBBBBBBBBBBBBBBBBB";

function approval(extra: Partial<ApprovalRecord> = {}): ApprovalRecord {
  return {
    id: "appr_1",
    run_id: "run_1",
    task_id: "manager_approval",
    hire_id: "h_acme_001",
    kind: "task",
    rule: null,
    status: "pending",
    approver_id: "u_acme_mgr_ravi",
    approver_role: null,
    title: "Manager approves equipment & access",
    items: ["MacBook Pro 14", "Bitbucket access"],
    packet: {
      summary: "Standard kit for an engineer.",
      recommendation: "approve",
      risk_flags: ["new_laptop"],
      cited_policies: ["kb_access_policy"],
    },
    requested_at: "2026-11-13T09:00:00Z",
    sla_hours: 48,
    decision: null,
    ...extra,
  };
}

function message(extra: Partial<MockOutboxMessage> = {}): MockOutboxMessage {
  return {
    id: "m_1",
    channel: "telegram",
    recipient_masked: "user:u_acme_mgr_ravi",
    text: "Approval needed: Manager approves equipment & access",
    buttons: [
      { text: "Approve", callback_data: BUTTON_APPROVE, decision: "approve" },
      { text: "Reject", callback_data: BUTTON_REJECT, decision: "reject" },
    ],
    run_id: "run_1",
    task_id: "manager_approval",
    message_id: 7,
    sent_at: "2026-11-13T09:00:01Z",
    audience_user_id: "u_acme_mgr_ravi",
    ...extra,
  };
}

function envelope(status: number, data: unknown, error: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "t", page: null } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const CONFLICT = { code: "CONFLICT", message: "This approval has already been decided.", details: [] };

interface FakeOptions {
  approvals?: ApprovalRecord[];
  outbox?: MockOutboxMessage[];
  decideStatus?: number;
  pressStatus?: number;
}

function fakeApi({ approvals = [approval()], outbox = [message()], decideStatus = 200, pressStatus = 200 }: FakeOptions) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const handler = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/api/, "");
    const text = await request.text();
    calls.push({ method: request.method, path: `${path}${url.search}`, body: text === "" ? null : JSON.parse(text) });
    if (path === "/approvals" && request.method === "GET") return envelope(200, approvals);
    if (path === "/mock-outbox" && request.method === "GET") return envelope(200, outbox);
    if (/^\/approvals\/[^/]+\/decision$/.test(path)) {
      return decideStatus === 200
        ? envelope(200, approval({ status: "approved" }))
        : envelope(decideStatus, null, CONFLICT);
    }
    if (/^\/mock-outbox\/[^/]+\/press$/.test(path)) {
      return pressStatus === 200 ? envelope(200, { ok: true }) : envelope(pressStatus, null, CONFLICT);
    }
    return envelope(404, null, { code: "NOT_FOUND", message: "Not found", details: [] });
  };
  return { handler, calls };
}

function renderAt(path: string, options: FakeOptions = {}, me: Me = RAVI) {
  const api = fakeApi(options);
  const client = createAppClient({
    session: createSessionStore(),
    onUnauthenticated: () => undefined,
    baseUrl: "http://web.test/api",
    fetch: api.handler,
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/approvals/*" element={<ApprovalsSection client={client} me={me} basePath="/approvals" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return api;
}

const writes = (api: ReturnType<typeof fakeApi>) => api.calls.filter((c) => c.method === "POST");

describe("Approvals inbox (FR-H2, U5)", () => {
  it("shows the AI-prepared packet and the request", async () => {
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-appr_1");
    expect(within(card).getByRole("heading", { name: /Manager approves equipment/ })).toBeVisible();
    expect(card).toHaveTextContent("Standard kit for an engineer.");
    expect(card).toHaveTextContent("MacBook Pro 14");
    expect(card).toHaveTextContent("new_laptop");
    expect(within(card).getByRole("link", { name: /run_1/ })).toHaveAttribute("href", "/runs/run_1");
  });

  it("approves in one action, with an optional comment", async () => {
    const api = renderAt("/approvals");
    const card = await screen.findByTestId("approval-appr_1");
    await userEvent.type(within(card).getByLabelText(/Comment/), "ok for Meera");
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(writes(api)).toEqual([
        { method: "POST", path: "/approvals/appr_1/decision", body: { decision: "approve", comment: "ok for Meera" } },
      ]),
    );
  });

  it("rejects without a comment", async () => {
    const api = renderAt("/approvals");
    await userEvent.click(within(await screen.findByTestId("approval-appr_1")).getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(writes(api)).toEqual([{ method: "POST", path: "/approvals/appr_1/decision", body: { decision: "reject" } }]),
    );
  });

  it('a second decision shows "already decided"', async () => {
    renderAt("/approvals", { decideStatus: 409 });
    const card = await screen.findByTestId("approval-appr_1");
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent(/already decided/i);
  });

  it("a decided approval shows its decision and no buttons", async () => {
    renderAt("/approvals", {
      approvals: [
        approval({
          status: "approved",
          decision: {
            decision: "approve",
            channel: "telegram",
            comment: null,
            decided_by: "u_acme_mgr_ravi",
            decided_at: "2026-11-13T09:05:00Z",
          },
        }),
      ],
    });
    const card = await screen.findByTestId("approval-appr_1");
    expect(within(card).getByTestId("approval-status")).toHaveAttribute("data-status", "approved");
    expect(card).toHaveTextContent(/telegram/);
    expect(within(card).queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("says when nothing waits", async () => {
    renderAt("/approvals", { approvals: [] });
    expect(await screen.findByText(/No approvals/)).toBeVisible();
  });

  it("asks for the caller's own approvals", async () => {
    const api = renderAt("/approvals");
    await screen.findByTestId("approval-appr_1");
    expect(api.calls[0]?.path).toBe("/approvals?mine=true");
  });
});

describe("Mock Telegram outbox (FR-N2)", () => {
  it("presses Approve through the authenticated press route, never the webhook itself", async () => {
    const api = renderAt("/approvals/outbox");
    const row = await screen.findByTestId("outbox-m_1");
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(writes(api)).toEqual([
        { method: "POST", path: "/mock-outbox/m_1/press", body: { callback_data: BUTTON_APPROVE } },
      ]),
    );
    expect(await within(row).findByRole("status")).toHaveTextContent(/webhook/i);
    expect(api.calls.some((c) => c.path.includes("/webhooks/"))).toBe(false);
  });

  it("presses Reject with the reject button's id", async () => {
    const api = renderAt("/approvals/outbox");
    await userEvent.click(within(await screen.findByTestId("outbox-m_1")).getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(writes(api)).toEqual([
        { method: "POST", path: "/mock-outbox/m_1/press", body: { callback_data: BUTTON_REJECT } },
      ]),
    );
  });

  it("shows the buttons only on messages addressed to the signed-in user", async () => {
    renderAt("/approvals/outbox", { outbox: [message({ id: "m_2", audience_user_id: "u_acme_mgr_other" })] });
    const row = await screen.findByTestId("outbox-m_2");
    expect(row).toHaveTextContent("Approval needed");
    expect(within(row).queryByRole("button")).toBeNull();
    expect(row).toHaveTextContent(/addressed to someone else/i);
  });

  it('a press on a decided approval shows "already decided"', async () => {
    renderAt("/approvals/outbox", { pressStatus: 409 });
    const row = await screen.findByTestId("outbox-m_1");
    await userEvent.click(within(row).getByRole("button", { name: "Approve" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent(/already decided/i);
  });

  it("links between the inbox and the outbox", async () => {
    renderAt("/approvals");
    const nav = await screen.findByRole("navigation", { name: "Approvals views" });
    expect(within(nav).getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/approvals");
    expect(within(nav).getByRole("link", { name: "Mock Telegram outbox" })).toHaveAttribute("href", "/approvals/outbox");
  });
});
