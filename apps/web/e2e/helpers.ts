/** Shared steps for the Playwright specs. Synthetic seed users only (`onb_api.testing.SEED_USERS`). */
import { expect, type Page } from "@playwright/test";

export const PASSWORD = process.env.E2E_PASSWORD ?? "";

export const USERS = {
  HR_ADMIN: "u_acme_hradmin@example.com",
  HR: "u_acme_hr1@example.com",
  MANAGER: "u_acme_mgr_ravi@example.com",
} as const;

export async function signInAs(page: Page, role: keyof typeof USERS) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(USERS[role]);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("role")).toBeVisible();
}

/** Opens a section through the nav: a full page load would drop the in-memory token (ADR 0005). */
export async function openSection(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}

export async function goInApp(page: Page, path: string) {
  await page.evaluate((target) => {
    window.history.pushState({}, "", target);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
}

/** Synthetic values in the shapes HR types them (PII-3's reserved test ranges, example.com). */
export const RAW = {
  email: "asha.verma.e2e@example.com",
  mobile: "+91 90000 00042",
  aadhaar: "9999 0000 4202",
  pan: "BQVPS4821K",
  bank_account: "000123456789012",
  ifsc: "HDFC0001234",
  account_holder_name: "Lotus Grove Holdings",
} as const;

/** A unique name (and email) per test: the API server is shared by parallel tests. */
export function uniqueHire() {
  const tag = Math.random().toString(36).slice(2, 8);
  return { name: `Asha ${tag}`, email: `asha.${tag}@example.com` };
}

/** Fills and submits the add-hire form; resolves on the new hire's page. */
export async function createHire(page: Page, hire: { name: string; email: string }, options: { aadhaar?: boolean } = {}) {
  await openSection(page, "Hires");
  await page.getByLabel("Name", { exact: true }).fill(hire.name);
  await page.getByLabel("Role", { exact: true }).fill("Engineer");
  await page.getByLabel("Location").fill("Pune");
  await page.getByLabel("Start date").fill("2026-12-01");
  await page.getByLabel("Manager ID").fill("u_acme_mgr_ravi");
  await page.getByLabel("Email", { exact: true }).fill(hire.email);
  await page.getByLabel("Mobile").fill(RAW.mobile);
  await page.getByLabel("PAN").fill(RAW.pan);
  await page.getByLabel("Bank account").fill(RAW.bank_account);
  await page.getByLabel("IFSC").fill(RAW.ifsc);
  await page.getByLabel("Account holder name").fill(RAW.account_holder_name);
  if (options.aadhaar ?? true) {
    await page.getByLabel("Aadhaar", { exact: true }).fill(RAW.aadhaar);
    await page.getByLabel("Aadhaar consent purpose").fill("statutory onboarding records");
  }
  await page.getByRole("button", { name: "Create hire" }).click();
  await expect(page.getByRole("heading", { level: 1, name: hire.name })).toBeVisible();
}
