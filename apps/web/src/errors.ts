/**
 * Envelope-aware error text. An `ApiError` carries the API's stable code and its client-safe
 * message (C-1); anything else is a bug or a network failure, and its text is never shown.
 */
import { ApiError, type ErrorDetail } from "@onb/ts-api-client";
import { ClockSkewError } from "./api";

export interface DescribedError {
  message: string;
  fields: readonly ErrorDetail[];
  traceId: string | null;
}

export type ErrorContext = "login" | "default";

const GENERIC = "Something went wrong. Try again.";

function messageFor(error: ApiError, context: ErrorContext): string {
  switch (error.code) {
    case "UNAUTHENTICATED":
      return context === "login" ? "Email or password is incorrect." : "Your session has ended. Sign in again.";
    case "FORBIDDEN":
      return "You don't have access to this.";
    case "VALIDATION_ERROR":
      return "Some fields need attention.";
    case "RATE_LIMITED":
      return "Too many attempts. Wait a moment and try again.";
    default:
      return error.message;
  }
}

export function describeError(error: unknown, context: ErrorContext = "default"): DescribedError {
  if (error instanceof ApiError) {
    return { message: messageFor(error, context), fields: error.details, traceId: error.traceId };
  }
  if (error instanceof ClockSkewError) {
    return { message: "This device's clock looks wrong. Check its date and time, then sign in again.", fields: [], traceId: null };
  }
  return { message: GENERIC, fields: [], traceId: null };
}
