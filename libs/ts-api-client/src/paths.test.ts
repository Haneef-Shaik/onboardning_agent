import { describe, expect, it } from "vitest";
import { apiPath } from "./paths";

describe("apiPath", () => {
  it("prefixes a path with the API prefix exactly once", () => {
    expect(apiPath("/runs")).toBe("/api/runs");
    expect(apiPath("runs")).toBe("/api/runs");
  });
});
