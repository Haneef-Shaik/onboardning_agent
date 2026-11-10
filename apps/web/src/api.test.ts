import { ApiError, createApiClient } from "@onb/ts-api-client";
import { describe, expect, it } from "vitest";
import { fetchHealth } from "./api";

function respondWith(status: number, body: unknown): (input: Request) => Promise<Response> {
  return () =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

describe("fetchHealth", () => {
  it("returns the typed health data from the envelope", async () => {
    const client = createApiClient({
      baseUrl: "http://web.test/api",
      fetch: respondWith(200, {
        success: true,
        data: { status: "ok", service: "api" },
        error: null,
        meta: { trace_id: null, page: null },
      }),
    });

    const health = await fetchHealth(client);

    expect(health.status).toBe("ok");
    expect(health.service).toBe("api");
  });

  it("throws ApiError with the envelope's code", async () => {
    const client = createApiClient({
      baseUrl: "http://web.test/api",
      fetch: respondWith(503, {
        success: false,
        data: null,
        error: { code: "SERVICE_UNAVAILABLE", message: "Service unavailable", details: [] },
        meta: { trace_id: null, page: null },
      }),
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
