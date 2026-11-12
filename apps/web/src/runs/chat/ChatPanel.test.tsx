import type { ChatMessageRecord, Role } from "@onb/ts-api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { createAppClient } from "../../api";
import { createSessionStore } from "../../auth/session";
import { ChatPanel } from "./ChatPanel";

afterEach(cleanup);

const PROPOSAL_ID = "prop_0123456789abcdef01234567";

function message(extra: Partial<ChatMessageRecord> & Pick<ChatMessageRecord, "role" | "content">): ChatMessageRecord {
  return { id: `m_${Math.random().toString(36).slice(2)}`, created_at: "2026-11-13T09:00:00Z", citations: [], ...extra };
}

function envelope(status: number, data: unknown, error: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "t", page: null } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function sse(reply: ChatMessageRecord): Response {
  const text = `event: message\ndata: ${JSON.stringify({ message: reply })}\n\nevent: end\ndata: {"status":"ok"}\n\n`;
  return new Response(text, { headers: { "content-type": "text/event-stream" } });
}

interface Script {
  reply: ChatMessageRecord;
  confirmStatus?: number;
}

function fakeApi(script: Script) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let stored: ChatMessageRecord[] = []; // what GET returns, as the real API keeps the conversation
  const handler = async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname.replace(/^\/api/, "");
    const text = request.method === "POST" ? await request.text() : "";
    calls.push({ method: request.method, path, body: text === "" ? null : JSON.parse(text) });
    if (path === "/runs/run_1/chat" && request.method === "GET") return envelope(200, stored);
    if (path === "/runs/run_1/chat" && request.method === "POST") {
      stored = [...stored, message({ role: "user", content: String((calls.at(-1)?.body as { message: string }).message) }), script.reply];
      return sse(script.reply);
    }
    if (path === "/runs/run_1/chat/confirm") {
      if (script.confirmStatus && script.confirmStatus !== 200) {
        return envelope(script.confirmStatus, null, { code: "CONFLICT", message: "This proposal was already used or has expired", details: [] });
      }
      stored = stored.map((m) => (m.proposal ? { ...m, proposal: { ...m.proposal, status: "confirmed" as const } } : m));
      return envelope(200, { id: "run_1" });
    }
    return envelope(404, null, { code: "NOT_FOUND", message: "Not found", details: [] });
  };
  return { handler, calls };
}

function renderPanel(role: Role, script: Script, onRunChanged: () => void = () => undefined) {
  const api = fakeApi(script);
  const client = createAppClient({
    session: createSessionStore(),
    onUnauthenticated: () => undefined,
    baseUrl: "http://web.test/api",
    fetch: api.handler,
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ChatPanel client={client} role={role} runId="run_1" onRunChanged={onRunChanged} />
    </QueryClientProvider>,
  );
  return api;
}

const answer = message({
  role: "assistant",
  content: "Task b failed: the sink rejected it.",
  status: "ok",
  citations: [{ run_id: "run_1", task_id: "b" }],
});

const proposing = message({
  role: "assistant",
  content: "I can retry task b.",
  status: "ok",
  citations: [{ run_id: "run_1", task_id: "b" }],
  proposal: {
    proposal_id: PROPOSAL_ID,
    type: "retry_task",
    run_id: "run_1",
    task_id: "b",
    requires_confirmation: true,
    status: "pending",
    expires_at: "2026-11-13T09:15:00Z",
  },
});

async function ask(text: string) {
  await userEvent.type(await screen.findByLabelText("Ask about this run"), text);
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("ChatPanel: answers and citations (FR-C1)", () => {
  it("shows the answer with each citation as a link to that task", async () => {
    const api = renderPanel("HR", { reply: answer });
    await ask("why is it stuck?");
    const reply = await screen.findByTestId("chat-answer");
    expect(reply).toHaveTextContent("the sink rejected it");
    expect(within(reply).getByRole("link", { name: "b" })).toHaveAttribute("href", "#task-b");
    expect(api.calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/runs/run_1/chat", body: { message: "why is it stuck?" } },
    ]);
  });

  it("does not send an empty question", async () => {
    renderPanel("HR", { reply: answer });
    expect(await screen.findByRole("button", { name: "Ask" })).toBeDisabled();
  });
});

describe("ChatPanel: a proposed action needs confirmation (FR-C2)", () => {
  it("opens a dialog and calls nothing until Confirm", async () => {
    const api = renderPanel("HR", { reply: proposing });
    await ask("retry it");
    await userEvent.click(await screen.findByRole("button", { name: "Review proposed retry" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("task b");
    expect(api.calls.some((c) => c.path.endsWith("/confirm"))).toBe(false);

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.calls.some((c) => c.path.endsWith("/confirm"))).toBe(false);
  });

  it("confirms exactly the proposal shown, once, and tells the run page", async () => {
    let changed = 0;
    const api = renderPanel("HR", { reply: proposing }, () => {
      changed += 1;
    });
    await ask("retry it");
    await userEvent.click(await screen.findByRole("button", { name: "Review proposed retry" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm retry" }));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.path.endsWith("/confirm"))).toEqual([
        { method: "POST", path: "/runs/run_1/chat/confirm", body: { proposal_id: PROPOSAL_ID } },
      ]),
    );
    await waitFor(() => expect(changed).toBe(1));
    expect(await screen.findByText("Retry confirmed")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Review proposed retry" })).toBeNull();
  });

  it("shows a used or expired proposal as an error and does not retry", async () => {
    renderPanel("HR", { reply: proposing, confirmStatus: 409 });
    await ask("retry it");
    await userEvent.click(await screen.findByRole("button", { name: "Review proposed retry" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm retry" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already used or has expired/);
  });

  it("offers no review button for an expired or already used proposal", async () => {
    if (!proposing.proposal) throw new Error("fixture has no proposal");
    const expired = { ...proposing, proposal: { ...proposing.proposal, status: "failed" as const } };
    renderPanel("HR", { reply: expired });
    await ask("retry it");
    expect(await screen.findByTestId("chat-answer")).toHaveTextContent("no longer");
    expect(screen.queryByRole("button", { name: "Review proposed retry" })).toBeNull();
  });

  it("gives a manager the answer but never a way to confirm", async () => {
    renderPanel("MANAGER", { reply: proposing });
    await ask("retry it");
    expect(await screen.findByTestId("chat-answer")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Review proposed retry" })).toBeNull();
  });
});

describe("ChatPanel: the dialog", () => {
  it("confirms the id of the proposal reviewed, not a constant or the first one", async () => {
    if (!proposing.proposal) throw new Error("fixture has no proposal");
    const secondId = "prop_aaaaaaaaaaaaaaaaaaaaaaaa";
    const second = { ...proposing, id: "m_second", proposal: { ...proposing.proposal, proposal_id: secondId } };
    const api = renderPanel("HR", { reply: second });
    await ask("retry it");
    await userEvent.click(await screen.findByRole("button", { name: "Review proposed retry" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm retry" }));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.path.endsWith("/confirm")).map((c) => c.body)).toEqual([
        { proposal_id: secondId },
      ]),
    );
  });

  it("closes on Escape and returns focus to the button that opened it", async () => {
    renderPanel("HR", { reply: proposing });
    await ask("retry it");
    const opener = await screen.findByRole("button", { name: "Review proposed retry" });
    await userEvent.click(opener);
    expect(screen.getByRole("alertdialog")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});
