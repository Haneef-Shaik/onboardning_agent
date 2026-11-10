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
export type LoginRequest = Schemas["LoginRequest"];
export type Me = Schemas["Me"];
export type Role = Schemas["Role"];
export type Token = Schemas["Token"];

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

/** The parts of an error body this client relies on; anything else may be missing (a proxy, an old API). */
interface LooseErrorEnvelope {
  success: false;
  error: { code: ErrorCode; message: string; details?: unknown };
  meta?: { trace_id?: unknown } | null;
}

function isErrorEnvelope(body: unknown): body is LooseErrorEnvelope {
  if (typeof body !== "object" || body === null) return false;
  const candidate = body as { success?: unknown; error?: { code?: unknown; message?: unknown } | null };
  return (
    candidate.success === false &&
    typeof candidate.error?.code === "string" &&
    typeof candidate.error.message === "string"
  );
}

function unexpected(status: number): ApiError {
  // The body is not shown: a proxy page or stack trace is not for the user.
  return new ApiError("INTERNAL_ERROR", `Unexpected response (${status})`, status, null, []);
}

function fromEnvelope(body: LooseErrorEnvelope, status: number): ApiError {
  const traceId = body.meta?.trace_id;
  const details = Array.isArray(body.error.details) ? (body.error.details as ErrorDetail[]) : [];
  return new ApiError(body.error.code, body.error.message, status, typeof traceId === "string" ? traceId : null, details);
}

/**
 * The `data` of a success envelope; throws `ApiError` for an error envelope or anything else.
 * For routes that answer with data. A success with `data: null` is treated as unexpected.
 */
export function unwrap<T>(result: FetchResult<T>): T {
  const { data, error, response } = result;
  if (error !== undefined) {
    throw isErrorEnvelope(error) ? fromEnvelope(error, response.status) : unexpected(response.status);
  }
  if (data?.success !== true || data.data === null || data.data === undefined) {
    throw unexpected(response.status);
  }
  return data.data;
}
