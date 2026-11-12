import { describe, expect, it } from "vitest";
import { EMPTY_FORM, buildNewHire, type HireFormValues } from "./new-hire";

const base: HireFormValues = {
  ...EMPTY_FORM,
  name: " Asha Verma ",
  role: "Engineer",
  location: "Pune",
  startDate: "2026-12-01",
  managerId: "u_acme_mgr_ravi",
  email: "asha@example.com",
};

describe("buildNewHire", () => {
  it("sends only what was filled in, trimmed", () => {
    const result = buildNewHire({ ...base, mobile: " 9876543210 ", pan: "" });

    expect(result).toEqual({
      ok: true,
      request: {
        name: "Asha Verma",
        role: "Engineer",
        location: "Pune",
        start_date: "2026-12-01",
        manager_id: "u_acme_mgr_ravi",
        notes: "",
        pii: { email: "asha@example.com", mobile: "9876543210" },
      },
    });
  });

  it("needs the hire's consent purpose whenever an Aadhaar is entered", () => {
    expect(buildNewHire({ ...base, aadhaar: "123412341234" })).toEqual({
      ok: false,
      errors: { aadhaarConsent: "Say what the Aadhaar is stored for; the hire must have consented." },
    });
    const result = buildNewHire({ ...base, aadhaar: "123412341234", aadhaarConsent: " payroll KYC " });
    expect(result.ok && result.request.aadhaar_consent).toEqual({ purpose: "payroll KYC" });
    expect(result.ok && result.request.pii.aadhaar).toBe("123412341234");
  });

  it("sends no consent without an Aadhaar, and the alternative ID only when chosen", () => {
    const withConsentOnly = buildNewHire({ ...base, aadhaarConsent: "payroll KYC" });
    expect(withConsentOnly.ok && "aadhaar_consent" in withConsentOnly.request).toBe(false);
    const alt = buildNewHire({ ...base, alternativeId: "passport" });
    expect(alt.ok && alt.request.alternative_id).toBe("passport");
  });

  it("reports every missing required field", () => {
    const result = buildNewHire(EMPTY_FORM);
    expect(result.ok).toBe(false);
    expect(!result.ok && Object.keys(result.errors).sort()).toEqual(
      ["email", "location", "managerId", "name", "role", "startDate"].sort(),
    );
  });
});
