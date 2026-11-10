/**
 * The typed API client (PRD §11 Contracts). Types come from `./generated/schema`, which
 * `make contracts` generates from the API's OpenAPI document; never edit that file by hand.
 */
import createClient, { type Client, type ClientOptions } from "openapi-fetch";
import type { components, paths } from "./generated/schema";
import { API_PREFIX } from "./paths";

export type { components, paths } from "./generated/schema";

type Schemas = components["schemas"];
export type ErrorCode = Schemas["ErrorCode"];
export type ErrorDetail = Schemas["ErrorDetail"];
export type ErrorEnvelope = Schemas["ErrorEnvelope"];
export type Meta = Schemas["Meta"];
export type PageMeta = Schemas["PageMeta"];
export type Health = Schemas["Health"];
export type HealthEnvelope = Schemas["HealthEnvelope"];

export type ApiClient = Client<paths>;

/** A client for the API; `baseUrl` defaults to `/api` (same origin, behind the proxy). */
export function createApiClient(options: ClientOptions = {}): ApiClient {
  return createClient<paths>({ baseUrl: API_PREFIX, ...options });
}

/** An error answer from the API, or a response that was not the envelope at all. */
export class ApiError extends Error {
  readonly code: ErrorCode;
  readonly status: number;
  readonly traceId: string | null;
  readonly details: readonly ErrorDetail[];

  constructor(code: ErrorCode, message: string, status: number, traceId: string | null, details: readonly ErrorDetail[]) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.traceId = traceId;
    this.details = details;
  }
}

interface SuccessShape<T> {
  success: boolean;
  data: T | null;
}

interface FetchResult<T> {
  data?: SuccessShape<T>;
  error?: unknown;
  response: Response;
}

function isErrorEnvelope(body: unknown): body is ErrorEnvelope & { error: Schemas["ErrorBody"] } {
  if (typeof body !== "object" || body === null) return false;
  const candidate = body as Partial<ErrorEnvelope>;
  return candidate.success === false && typeof candidate.error?.code === "string";
}

function unexpected(status: number): ApiError {
  // The body is not shown: a proxy page or stack trace is not for the user.
  return new ApiError("INTERNAL_ERROR", `Unexpected response (${status})`, status, null, []);
}

/** The `data` of a success envelope; throws `ApiError` for an error envelope or anything else. */
export function unwrap<T>(result: FetchResult<T>): T {
  const { data, error, response } = result;
  if (error !== undefined) {
    if (!isErrorEnvelope(error)) throw unexpected(response.status);
    const { code, message, details } = error.error;
    throw new ApiError(code, message, response.status, error.meta.trace_id, details);
  }
  if (data?.success !== true || data.data === null || data.data === undefined) {
    throw unexpected(response.status);
  }
  return data.data;
}
