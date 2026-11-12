import { describe, expect, it } from "vitest";
import { canControlRuns, canOpenTrace, controlsFor, traceUrl, whyBlocked } from "./policy";

describe("who may do what on a run", () => {
  it("lets HR and HR_ADMIN control runs and open traces, never a manager", () => {
    expect(canControlRuns("HR")).toBe(true);
    expect(canControlRuns("HR_ADMIN")).toBe(true);
    expect(canControlRuns("MANAGER")).toBe(false);
    expect(canOpenTrace("HR")).toBe(true);
    expect(canOpenTrace("HR_ADMIN")).toBe(true);
    expect(canOpenTrace("MANAGER")).toBe(false);
  });
});

describe("controlsFor (what the run's state allows; the API still decides)", () => {
  it.each([
    ["started", ["pause", "cancel"]],
    ["running", ["pause", "cancel"]],
    ["paused", ["resume", "cancel"]],
    ["blocked", ["cancel"]],
    ["completed", []],
    ["failed", []],
    ["cancelled", []],
  ] as const)("%s", (status, expected) => {
    expect(controlsFor(status)).toEqual(expected);
  });
});

describe("traceUrl", () => {
  it("opens Jaeger on the run's trace id", () => {
    expect(traceUrl("https://jaeger.localhost", "4bf92f3577b34da6a3ce929d0e0e4736")).toBe(
      "https://jaeger.localhost/trace/4bf92f3577b34da6a3ce929d0e0e4736",
    );
    expect(traceUrl("https://jaeger.localhost:19801/", "4bf92f3577b34da6a3ce929d0e0e4736")).toBe(
      "https://jaeger.localhost:19801/trace/4bf92f3577b34da6a3ce929d0e0e4736",
    );
  });

  it("gives no link without a well-formed trace id", () => {
    expect(traceUrl("https://jaeger.localhost", null)).toBeNull();
    expect(traceUrl("https://jaeger.localhost", "../x")).toBeNull();
    expect(traceUrl("https://jaeger.localhost", "ABC")).toBeNull();
  });
});

describe("whyBlocked", () => {
  it("explains the causes the platform records, and shows an unknown code as it is", () => {
    expect(whyBlocked("sink_rejected")).toMatch(/rejected/i);
    expect(whyBlocked("LeaseExpired")).toMatch(/worker/i);
    expect(whyBlocked("budget_exceeded")).toMatch(/budget/i);
    expect(whyBlocked("StubTaskError")).toBe("Failed: StubTaskError");
    expect(whyBlocked(null)).toBe("Failed, no cause recorded");
  });
});
