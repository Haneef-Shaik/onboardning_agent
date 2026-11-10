import { describe, expect, it } from "vitest";
import { ApiError, createApiClient, unwrap, type ErrorEnvelope, type HealthEnvelope } from "./index";

const TRACE = "0af7651916cd43dd8448eb211c80319c";

function fakeFetch(status: number, body: unknown, seen: Request[] = []): (input: Request) => Promise<Response> {
  return (input: Request) => {
    seen.push(input);
    const text = typeof body === "string" ? body : JSON.stringify(body);
    const contentType = typeof body === "string" ? "text/plain" : "application/json";
    return Promise.resolve(new Response(text, { status, headers: { "content-type": contentType } }));
  };
}

const health: HealthEnvelope = {
  success: true,
  data: { status: "ok", service: "api" },
  error: null,
  meta: { trace_id: TRACE, page: null },
};

const notFound: ErrorEnvelope = {
  success: false,
  data: null,
  error: { code: "NOT_FOUND", message: "Not found", details: [] },
  meta: { trace_id: TRACE, page: null },
};

describe("createApiClient", () => {
  it("calls the API under /api by default and returns the typed envelope", async () => {
    const seen: Request[] = [];
    const client = createApiClient({ baseUrl: "http://web.test/api", fetch: fakeFetch(200, health, seen) });

    const result = await client.GET("/health");

    expect(seen[0]?.url).toBe("http://web.test/api/health");
    expect(result.data?.data?.status).toBe("ok");
    expect(unwrap(result)).toEqual({ status: "ok", service: "api" });
  });
});

describe("unwrap", () => {
  it("throws an ApiError carrying the envelope's code, status, message and trace id", async () => {
    const client = createApiClient({ baseUrl: "http://web.test/api", fetch: fakeFetch(404, notFound) });

    const result = await client.GET("/health");

    expect(() => unwrap(result)).toThrow(ApiError);
    try {
      unwrap(result);
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiError = err as ApiError;
      expect(apiError.code).toBe("NOT_FOUND");
      expect(apiError.status).toBe(404);
      expect(apiError.message).toBe("Not found");
      expect(apiError.traceId).toBe(TRACE);
      expect(apiError.details).toEqual([]);
    }
  });

  it("treats a non-envelope error (a proxy page) as INTERNAL_ERROR without echoing the body", async () => {
    const client = createApiClient({ baseUrl: "http://web.test/api", fetch: fakeFetch(502, "<html>Bad gateway</html>") });

    const result = await client.GET("/health");

    expect(() => unwrap(result)).toThrow(/Unexpected response \(502\)/);
    try {
      unwrap(result);
    } catch (err) {
      const apiError = err as ApiError;
      expect(apiError.code).toBe("INTERNAL_ERROR");
      expect(apiError.traceId).toBeNull();
      expect(apiError.message).not.toContain("html");
    }
  });

  it("rejects a 2xx answer that is not a success envelope", async () => {
    const client = createApiClient({ baseUrl: "http://web.test/api", fetch: fakeFetch(200, { hello: "world" }) });

    const result = await client.GET("/health");

    expect(() => unwrap(result)).toThrow(ApiError);
  });
});
