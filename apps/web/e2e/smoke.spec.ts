/**
 * UI-1 smoke: each role signs in through the UI against the real API and sees its own nav.
 * Synthetic seed users (`onb_api.testing.SEED_USERS`); the password is made up per run.
 */
import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";

const CASES = [
  {
    role: "HR_ADMIN",
    email: "u_acme_hradmin@example.com",
    name: "Kavya Rao",
    badge: "HR admin",
    nav: ["Runs", "Approvals", "Hires", "Templates", "Change requests", "Admin"],
    refused: null,
  },
  {
    role: "HR",
    email: "u_acme_hr1@example.com",
    name: "Sameer Khan",
    badge: "HR",
    nav: ["Runs", "Approvals", "Hires", "Templates", "Change requests"],
    refused: "/admin",
  },
  {
    role: "MANAGER",
    email: "u_acme_mgr_ravi@example.com",
    name: "Ravi Kumar",
    badge: "Manager",
    nav: ["Runs", "Approvals", "Templates", "Change requests"],
    refused: "/hires",
  },
] as const;

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test.beforeAll(() => {
  expect(PASSWORD, "E2E_PASSWORD comes from playwright.config.ts").not.toBe("");
});

for (const c of CASES) {
  test(`${c.role} signs in and sees the ${c.role} nav`, async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith("/api/")) apiCalls.push(`${request.method()} ${url.pathname}`);
    });

    await signIn(page, c.email, PASSWORD);

    await expect(page.getByText(c.name)).toBeVisible();
    await expect(page.getByTestId("role")).toHaveText(c.badge);
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link")).toHaveText([...c.nav]);
    await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
    expect(apiCalls).toEqual(["POST /api/auth/login", "GET /api/me"]);

    if (c.refused !== null) {
      // A full page load drops the in-memory token (ADR 0005), so go there through the app.
      await page.evaluate((path) => {
        window.history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, c.refused);
      await expect(page.getByRole("heading", { name: "Not available" })).toBeVisible();
    }

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });
}

test("a wrong password shows the login error and does not sign in", async ({ page }) => {
  await signIn(page, "u_acme_hr1@example.com", `${PASSWORD}-wrong`);

  await expect(page.getByRole("alert")).toContainText("Email or password is incorrect.");
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
});

test("a reload signs the user out: the token is in memory only", async ({ page }) => {
  await signIn(page, "u_acme_hr1@example.com", PASSWORD);
  await expect(page.getByText("Sameer Khan")).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);

  await page.reload();

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
