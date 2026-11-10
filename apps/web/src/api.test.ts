import { ApiError, createApiClient } from "@onb/ts-api-client";
import { describe, expect, it, vi } from "vitest";
import { createAppClient, fetchHealth, fetchMe, login } from "./api";
import { createSessionStore } from "./auth/session";

type Handler = (request: Request) => Promise<Response>;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function ok(data: unknown): Response {
  return json(200, { success: true, data, error: null, meta: { trace_id: null, page: null } });
}

function fail(status: number, code: string, message: string): Response {
  return json(status, { success: false, data: null, error: { code, message, details: [] }, meta: { trace_id: "t1", page: null } });
}

const ME = { user_id: "u_acme_hr1", org_id: "org_acme", role: "HR", name: "Sameer Khan", email: "u_acme_hr1@example.com" };
const NOW_MS = 1_800_000_000_000;

function setup(handler: Handler) {
  const session = createSessionStore(() => NOW_MS);
  const onUnauthenticated = vi.fn();
  const requests: Request[] = [];
  const client = createAppClient({
    session,
    onUnauthenticated,
    baseUrl: "http://web.test/api",
    fetch: (request: Request) => {
      requests.push(request.clone());
      return handler(request);
    },
  });
  return { client, session, onUnauthenticated, requests };
}

describe("fetchHealth", () => {
  it("returns the typed health data from the envelope", async () => {
    const client = createApiClient({
      baseUrl: "http://web.test/api",
      fetch: () => Promise.resolve(ok({ status: "ok", service: "api" })),
    });

    const health = await fetchHealth(client);

    expect(health.status).toBe("ok");
    expect(health.service).toBe("api");
  });

  it("throws ApiError with the envelope's code", async () => {
    const client = createApiClient({
      baseUrl: "http://web.test/api",
      fetch: () => Promise.resolve(fail(503, "SERVICE_UNAVAILABLE", "Service unavailable")),
    });

    await expect(fetchHealth(client)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE", status: 503 });
    await expect(fetchHealth(client)).rejects.toBeInstanceOf(ApiError);
  });

  it("is typed by the generated contract: an unknown path does not compile", () => {
    const client = createApiClient({ baseUrl: "http://web.test/api" });
    // Typechecked, never called: proves `tsc` sees the generated paths, not `any`.
    const typeOnly = (): unknown =>
      // @ts-expect-error "/no-such-route" is not a path in the API's OpenAPI document
      client.GET("/no-such-route");
    expect(typeof typeOnly).toBe("function");
  });
});

describe("createAppClient", () => {
  it("sends no Authorization header without a session", async () => {
    const { client, requests } = setup(() => Promise.resolve(ok({ status: "ok", service: "api" })));

    await fetchHealth(client);

    expect(requests[0]?.headers.get("authorization")).toBeNull();
  });

  it("sends the session's bearer token", async () => {
    const { client, session, requests } = setup(() => Promise.resolve(ok(ME)));
    session.set({ accessToken: "tkn-1", expiresAt: NOW_MS / 1000 + 60 });

    await fetchMe(client);

    expect(requests[0]?.headers.get("authorization")).toBe("Bearer tkn-1");
  });

  it("ends the session and reports it when the API answers 401 to a signed-in call", async () => {
    const { client, session, onUnauthenticated } = setup(() =>
      Promise.resolve(fail(401, "UNAUTHENTICATED", "Authentication required")),
    );
    session.set({ accessToken: "tkn-1", expiresAt: NOW_MS / 1000 + 60 });

    await expect(fetchMe(client)).rejects.toMatchObject({ code: "UNAUTHENTICATED", status: 401 });

    expect(session.get()).toBeNull();
    expect(onUnauthenticated).toHaveBeenCalledOnce();
  });

  it("does not treat a 403 as a lost session", async () => {
    const { client, session, onUnauthenticated } = setup(() => Promise.resolve(fail(403, "FORBIDDEN", "Forbidden")));
    session.set({ accessToken: "tkn-1", expiresAt: NOW_MS / 1000 + 60 });

    await expect(fetchMe(client)).rejects.toMatchObject({ code: "FORBIDDEN" });

    expect(session.get()).not.toBeNull();
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });
});

describe("login", () => {
  it("posts the credentials and returns the token as a session", async () => {
    const { client, requests } = setup(() =>
      Promise.resolve(ok({ access_token: "tkn-2", token_type: "bearer", expires_at: 1_800_000_060 })),
    );

    const session = await login(client, "u_acme_hr1@example.com", "pw");

    expect(session).toEqual({ accessToken: "tkn-2", expiresAt: 1_800_000_060 });
    expect(requests[0]?.method).toBe("POST");
    expect(new URL(requests[0]?.url ?? "").pathname).toBe("/api/auth/login");
    expect(await requests[0]?.json()).toEqual({ email: "u_acme_hr1@example.com", password: "pw" });
  });

  it("does not report a wrong password as a lost session", async () => {
    const { client, onUnauthenticated } = setup(() =>
      Promise.resolve(fail(401, "UNAUTHENTICATED", "Authentication required")),
    );

    await expect(login(client, "u_acme_hr1@example.com", "wrong")).rejects.toMatchObject({ code: "UNAUTHENTICATED" });

    expect(onUnauthenticated).not.toHaveBeenCalled();
  });
});
