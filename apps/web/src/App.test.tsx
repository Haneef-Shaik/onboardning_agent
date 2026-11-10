import type { Role } from "@onb/ts-api-client";
import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { createAppClient } from "./api";
import { App } from "./App";
import { createSessionStore } from "./auth/session";

afterEach(cleanup);

// Synthetic users (evals/fixtures/seed_v1.json ids); the fake API accepts one made-up password.
const PASSWORD = "test-only-password";
const USERS: Record<string, { user_id: string; name: string; role: Role }> = {
  "u_acme_hradmin@example.com": { user_id: "u_acme_hradmin", name: "Kavya Rao", role: "HR_ADMIN" },
  "u_acme_hr1@example.com": { user_id: "u_acme_hr1", name: "Sameer Khan", role: "HR" },
  "u_acme_mgr_ravi@example.com": { user_id: "u_acme_mgr_ravi", name: "Ravi Kumar", role: "MANAGER" },
};

function envelope(status: number, data: unknown, error: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "trace-1", page: null } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const unauthenticated = () =>
  envelope(401, null, { code: "UNAUTHENTICATED", message: "Authentication required", details: [] });

/** A stand-in for the API's /auth/login and /me, keyed by an opaque token per user. */
function fakeApi() {
  const state = { expireTokens: false, ttlSeconds: 3600 };
  const handler = async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname;
    if (path === "/api/auth/login" && request.method === "POST") {
      const { email, password } = (await request.json()) as { email: string; password: string };
      if (!(email in USERS) || password !== PASSWORD) return unauthenticated();
      return envelope(200, { access_token: `tok:${email}`, token_type: "bearer", expires_at: Date.now() / 1000 + state.ttlSeconds });
    }
    if (path === "/api/me") {
      const email = request.headers.get("authorization")?.replace("Bearer tok:", "") ?? "";
      const user = USERS[email];
      if (!user || state.expireTokens) return unauthenticated();
      return envelope(200, { ...user, org_id: "org_acme", email });
    }
    return envelope(404, null, { code: "NOT_FOUND", message: "Not found", details: [] });
  };
  return { handler, state };
}

function renderApp(initialPath = "/") {
  const api = fakeApi();
  const session = createSessionStore();
  const client = createAppClient({ session, onUnauthenticated: () => undefined, baseUrl: "http://web.test/api", fetch: api.handler });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App client={client} session={session} queryClient={queryClient} />
    </MemoryRouter>,
  );
  return { api, session, queryClient };
}

async function signIn(email: string, password = PASSWORD) {
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

const navLabels = () =>
  within(screen.getByRole("navigation", { name: "Main" }))
    .getAllByRole("link")
    .map((link) => link.textContent);

describe("App", () => {
  it("sends a signed-out visitor to the sign-in page", async () => {
    renderApp("/runs");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
  });

  it.each([
    ["u_acme_hradmin@example.com", "Kavya Rao", ["Runs", "Approvals", "Hires", "Templates", "Change requests", "Admin"]],
    ["u_acme_hr1@example.com", "Sameer Khan", ["Runs", "Approvals", "Hires", "Templates", "Change requests"]],
    ["u_acme_mgr_ravi@example.com", "Ravi Kumar", ["Runs", "Approvals", "Templates", "Change requests"]],
  ])("signs in %s and shows that role's nav", async (email, name, expected) => {
    renderApp();

    await signIn(email);

    expect(await screen.findByText(name)).toBeInTheDocument();
    expect(navLabels()).toEqual(expected);
    expect(screen.getByRole("heading", { level: 1, name: "Runs" })).toBeInTheDocument();
  });

  it("shows a wrong password as a login error and stays signed out", async () => {
    const { session } = renderApp();

    await signIn("u_acme_hr1@example.com", "wrong-password");

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(session.get()).toBeNull();
  });

  it("refuses a section the role does not have, even by direct URL", async () => {
    renderApp("/admin");

    await signIn("u_acme_hr1@example.com");

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Admin" })).toBeNull();
  });

  it("returns to the page asked for after signing in", async () => {
    renderApp("/templates");

    await signIn("u_acme_mgr_ravi@example.com");

    expect(await screen.findByRole("heading", { level: 1, name: "Templates" })).toBeInTheDocument();
  });

  it("signs out on request and forgets the token", async () => {
    const { session } = renderApp();
    await signIn("u_acme_hr1@example.com");
    await screen.findByText("Sameer Khan");

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(session.get()).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the next user only their own profile and nav after a sign-out in the same tab", async () => {
    renderApp();
    await signIn("u_acme_hr1@example.com");
    await screen.findByText("Sameer Khan");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await signIn("u_acme_mgr_ravi@example.com");

    expect(await screen.findByText("Ravi Kumar")).toBeInTheDocument();
    expect(screen.queryByText("Sameer Khan")).toBeNull();
    expect(navLabels()).toEqual(["Runs", "Approvals", "Templates", "Change requests"]);
  });

  it("ends the session when the token expires, with a notice", async () => {
    const { api, session } = renderApp();
    api.state.ttlSeconds = 1;
    await signIn("u_acme_hr1@example.com");
    await screen.findByText("Sameer Khan");

    expect(await screen.findByRole("heading", { name: "Sign in" }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended. Sign in again.");
    expect(session.get()).toBeNull();
  });

  it("signs out with a notice when the API rejects the token", async () => {
    const { api, queryClient } = renderApp();
    await signIn("u_acme_hr1@example.com");
    await screen.findByText("Sameer Khan");

    api.state.expireTokens = true;
    // Any signed-in call that gets 401 ends the session; here, the profile being refetched.
    await act(() => queryClient.invalidateQueries());

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended. Sign in again.");
  });
});
