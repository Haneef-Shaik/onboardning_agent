import { ApiError, type ErrorCode } from "@onb/ts-api-client";
import { describe, expect, it } from "vitest";
import { ClockSkewError } from "./api";
import { describeError } from "./errors";

function apiError(code: ErrorCode, message = "Server text", details: { field: string; issue: string }[] = []): ApiError {
  return new ApiError(code, message, 400, "trace-1", details);
}

describe("describeError", () => {
  it("explains a failed login without saying which part was wrong", () => {
    expect(describeError(apiError("UNAUTHENTICATED"), "login").message).toBe("Email or password is incorrect.");
  });

  it("explains a lost session elsewhere", () => {
    expect(describeError(apiError("UNAUTHENTICATED")).message).toBe("Your session has ended. Sign in again.");
  });

  it("explains a refusal", () => {
    expect(describeError(apiError("FORBIDDEN")).message).toBe("You don't have access to this.");
  });

  it("lists the fields a validation error names", () => {
    const described = describeError(apiError("VALIDATION_ERROR", "Invalid", [{ field: "body.email", issue: "too short" }]));
    expect(described.message).toBe("Some fields need attention.");
    expect(described.fields).toEqual([{ field: "body.email", issue: "too short" }]);
  });

  it("asks the user to wait when rate limited", () => {
    expect(describeError(apiError("RATE_LIMITED")).message).toBe("Too many attempts. Wait a moment and try again.");
  });

  it("uses the API's client-safe message for other codes, with the trace id for support", () => {
    const described = describeError(apiError("CONFLICT", "Already exists"));
    expect(described.message).toBe("Already exists");
    expect(described.traceId).toBe("trace-1");
  });

  it("explains a token the device's clock already sees as expired", () => {
    expect(describeError(new ClockSkewError()).message).toBe(
      "This device's clock looks wrong. Check its date and time, then sign in again.",
    );
  });

  it("never shows the text of an error that is not from the API", () => {
    const described = describeError(new Error("TypeError at secret.ts:12"));
    expect(described.message).toBe("Something went wrong. Try again.");
    expect(described.traceId).toBeNull();
  });
});
