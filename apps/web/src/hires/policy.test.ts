import type { PiiField, Role } from "@onb/ts-api-client";
import { describe, expect, it } from "vitest";
import { PII_FIELDS, canRevealField } from "./policy";

const FIELDS = PII_FIELDS.map((f) => f.field);

describe("canRevealField (mirrors the API: HR/HR_ADMIN reveal, Aadhaar HR_ADMIN only)", () => {
  it("lists every sensitive field the API knows", () => {
    const known: PiiField[] = ["mobile", "email", "aadhaar", "pan", "bank_account", "ifsc", "account_holder_name"];
    expect([...FIELDS].sort()).toEqual([...known].sort());
  });

  it("lets HR_ADMIN reveal every field", () => {
    expect(FIELDS.every((field) => canRevealField("HR_ADMIN", field))).toBe(true);
  });

  it("lets HR reveal every field but Aadhaar", () => {
    expect(FIELDS.filter((field) => canRevealField("HR", field))).toEqual(FIELDS.filter((f) => f !== "aadhaar"));
    expect(canRevealField("HR", "aadhaar")).toBe(false);
  });

  it("never lets a manager reveal anything", () => {
    expect(FIELDS.some((field) => canRevealField("MANAGER", field))).toBe(false);
  });

  it("refuses an unknown role", () => {
    expect(canRevealField("INTERN" as Role, "mobile")).toBe(false);
  });
});
