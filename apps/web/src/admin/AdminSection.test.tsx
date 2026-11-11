import { QueryClient } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { createAppClient } from "../api";
import { App } from "../App";
import { createSessionStore } from "../auth/session";
import { ACCESS, DENIAL, fakeAdminApi, PASSWORD, RUN } from "./fakeAdminApi";
import { formatRate, formatWhen } from "./format";

afterEach(cleanup);

const ADMIN = "u_acme_hradmin@example.com";

async function openAs(path: string, email = ADMIN) {
  const api = fakeAdminApi();
  const session = createSessionStore();
  const client = createAppClient({ session, onUnauthenticated: () => undefined, baseUrl: "http://web.test/api", fetch: api.handler });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[path]}>
      <App client={client} session={session} queryClient={queryClient} />
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  return api;
}

const adminCalls = (api: ReturnType<typeof fakeAdminApi>) =>
  api.requests.map((url) => url.pathname).filter((path) => !["/api/auth/login", "/api/me"].includes(path));

const rowsOf = (label: string) => within(screen.getByRole("table", { name: label })).getAllByRole("row").slice(1);

describe("Admin section", () => {
  it("opens on the agents list with each agent's connector and model", async () => {
    await openAs("/admin");

    expect(await screen.findByRole("heading", { level: 1, name: "Agents" })).toBeInTheDocument();
    await screen.findByRole("table", { name: "Agents" });
    const [row] = rowsOf("Agents");
    expect(row).toHaveTextContent("db_agent");
    expect(row).toHaveTextContent("fake");
    expect(row).toHaveTextContent("fake-standard-model");
    expect(within(screen.getByRole("navigation", { name: "Admin" })).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Agents",
      "Audit log",
      "PII access log",
      "Eval runs",
    ]);
  });

  it("shows an agent's connector, model, allowed tools and recent denials from the audit log", async () => {
    const api = await openAs("/admin/agents/db_agent");

    expect(await screen.findByTestId("agent-connector")).toHaveTextContent("fake");
    expect(screen.getByTestId("agent-model")).toHaveTextContent("fake-standard-model");
    const [mongo] = rowsOf("Allowed MCP tools");
    expect(mongo).toHaveTextContent("find, aggregate, count, update_one");
    expect(mongo).toHaveTextContent("run_tasks");
    expect(screen.getByText("date_calc")).toBeInTheDocument();
    const [denial] = await screen.findAllByRole("row", { name: /collection_not_in_manifest/ });
    expect(denial).toHaveTextContent("tool:mongo.find/other");
    expect(denial).toHaveTextContent(formatWhen(DENIAL.at));
    const query = api.requests.find((url) => url.pathname === "/api/audit")?.searchParams;
    expect(Object.fromEntries(query ?? [])).toEqual({
      action: "mcp.tool_call",
      outcome: "denied",
      actor_type: "agent",
      actor_id: "db_agent",
      limit: "10",
    });
  });

  it("lists the audit log, pages it and sends the filters", async () => {
    const api = await openAs("/admin/audit");

    expect(await screen.findByText("pii.reveal")).toBeInTheDocument();
    expect(rowsOf("Audit log")).toHaveLength(2);
    expect(screen.getByText("Showing 1–50 of 120")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "denied");
    await userEvent.type(screen.getByLabelText("Action"), "mcp.tool_call");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    const audits = api.requests.filter((url) => url.pathname === "/api/audit").map((url) => Object.fromEntries(url.searchParams));
    expect(audits).toContainEqual({ page: "2", limit: "50" });
    expect(audits.at(-1)).toEqual({ page: "1", limit: "50", action: "mcp.tool_call", outcome: "denied" });
  });

  it("shows the PII access log masked, and never the vault token", async () => {
    await openAs("/admin/pii-access");

    const [row] = await screen.findAllByRole("row", { name: /h_acme_001/ });
    expect(row).toHaveTextContent(ACCESS.masked);
    expect(row).toHaveTextContent("payroll check");
    expect(document.body.textContent).not.toContain(ACCESS.token);
    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
  });

  it("lists eval runs and opens one with its gates, metrics and cases", async () => {
    await openAs("/admin/evals");

    await userEvent.click(await screen.findByRole("link", { name: RUN.id }));

    expect(await screen.findByRole("table", { name: "Gates" })).toHaveTextContent("50.0% < 80.0%");
    expect(rowsOf("Metrics")[0]).toHaveTextContent(formatRate(0.8333));
    expect(rowsOf("Cases")[0]).toHaveTextContent("2/3");
  });

  it.each(["u_acme_hr1@example.com", "u_acme_mgr_ravi@example.com"])(
    "refuses %s every admin view without calling an admin route",
    async (email) => {
      for (const path of ["/admin", "/admin/agents/db_agent", "/admin/audit", "/admin/pii-access", "/admin/evals"]) {
        const api = await openAs(path, email);
        expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
        expect(adminCalls(api)).toEqual([]);
        cleanup();
      }
    },
  );

  it("shows the API's refusal as an error, not as empty data", async () => {
    const api = fakeAdminApi();
    api.state.forbidAdmin = true;
    const session = createSessionStore();
    const client = createAppClient({ session, onUnauthenticated: () => undefined, baseUrl: "http://web.test/api", fetch: api.handler });
    render(
      <MemoryRouter initialEntries={["/admin/agents"]}>
        <App client={client} session={session} queryClient={new QueryClient({ defaultOptions: { queries: { retry: false } } })} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("Email"), ADMIN);
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No agents are registered.")).toBeNull();
  });

  it("formats times in UTC and rates as percentages", () => {
    expect(formatWhen("2026-11-01T08:05:00Z")).toBe("2026-11-01 08:05 UTC");
    expect(formatWhen("not a date")).toBe("not a date");
    expect(formatRate(0.5)).toBe("50.0%");
    expect(formatRate(null)).toBe("—");
  });
});
