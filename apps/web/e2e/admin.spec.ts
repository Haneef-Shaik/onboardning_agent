/**
 * UI-8: the admin views against the real API (`serve_api.py` with `admin_seed.py`'s data).
 * HR_ADMIN signs in through the form and opens each view; an agent's page shows its connector, model,
 * allowed tools and recent denials from the audit log; HR and MANAGER are refused every admin route.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";
const ADMIN = "u_acme_hradmin@example.com";
// admin_seed.py: the synthetic hire's raw values, which must never reach the page.
const RAW_VALUES = ["AEEPI7101K", "meera.iyer.e2e@example.com", "90000 00071"];
const AGENTS = ["approvals_agent", "db_agent", "provisioning_agent", "run_assistant", "template_builder", "templates_agent"];
const RUNS = ["evr_20261103T090000Z_0e2e0002", "evr_20261102T090000Z_0e2e0001"] as const;
const ADMIN_ROUTES = ["/agents", "/agents/db_agent", "/audit", "/pii-access-log", "/evals/runs", `/evals/runs/${RUNS[0]}`];

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

/** A full page load signs out (ADR 0005), so move within the app. */
async function goWithin(page: Page, path: string) {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
}

const rows = (page: Page, table: string) => page.getByRole("table", { name: table }).locator("tbody tr");

async function tokenFor(request: APIRequestContext, email: string): Promise<string> {
  const response = await request.post("/api/auth/login", { data: { email, password: PASSWORD } });
  expect(response.status()).toBe(200);
  return ((await response.json()) as { data: { access_token: string } }).data.access_token;
}

test.beforeAll(() => {
  expect(PASSWORD, "E2E_PASSWORD comes from playwright.config.ts").not.toBe("");
});

test("HR_ADMIN opens each admin view and sees seeded data", async ({ page }) => {
  await signIn(page, ADMIN);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Admin" }).click();
  const views = page.getByRole("navigation", { name: "Admin" });

  // Agents: every registered agent, each with its resolved connector and model.
  await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();
  await expect(rows(page, "Agents").locator("td:first-child")).toHaveText(AGENTS);
  await expect(rows(page, "Agents").filter({ hasText: "db_agent" })).toContainText("e2e-standard-model");

  // Audit log: the hub's denials and what the hire service audited.
  await views.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Audit log" })).toBeVisible();
  const audit = rows(page, "Audit log");
  await expect(audit.filter({ hasText: "pii.reveal" })).toHaveCount(1);
  await expect(audit.filter({ hasText: "hire.create" })).toHaveCount(1);
  await expect(audit.filter({ hasText: "agent:db_agent" })).toHaveCount(3);
  await page.getByLabel("Outcome").selectOption("denied");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(audit).toHaveCount(3);

  // PII access log: the seeded reveal, masked; never a raw value or a vault token.
  await views.getByRole("link", { name: "PII access log" }).click();
  const access = rows(page, "PII access log");
  await expect(access).toHaveCount(1);
  await expect(access).toContainText("payroll setup");
  await expect(access).toContainText("pan");
  await expect(access).toContainText("user:u_acme_hr1");

  // Eval runs: both seeded runs, newest first; one opens with its gates, metrics and cases.
  await views.getByRole("link", { name: "Eval runs" }).click();
  await expect(rows(page, "Eval runs").locator("td:first-child")).toHaveText(RUNS);
  await page.getByRole("link", { name: RUNS[1] }).click();
  await expect(page.getByRole("table", { name: "Gates" })).toContainText("50.0% >= 50.0%");
  await expect(rows(page, "Metrics")).toContainText(["83.3%"]);
  await expect(rows(page, "Cases")).toHaveText([/db_agent-001.*3\/3/, /db_agent-002.*2\/3/]);

  const text = (await page.content()) + (await page.evaluate(() => document.body.innerText));
  for (const value of [...RAW_VALUES, "ptk_"]) expect(text).not.toContain(value);
});

test("an agent's page shows its connector, model, allowed tools and recent denials from the audit log", async ({ page }) => {
  const auditQueries: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/audit") auditQueries.push(url.searchParams);
  });
  await signIn(page, ADMIN);
  await goWithin(page, "/admin/agents");
  await page.getByRole("link", { name: "db_agent" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Agent db_agent" })).toBeVisible();
  await expect(page.getByTestId("agent-connector")).toHaveText("fake");
  await expect(page.getByTestId("agent-model")).toHaveText("e2e-standard-model");
  const mongo = rows(page, "Allowed MCP tools").filter({ hasText: "mongo" });
  await expect(mongo).toContainText("find, aggregate, count, update_one");
  await expect(mongo).toContainText("run_tasks, documents_meta");
  await expect(page.getByText("date_calc")).toBeVisible();

  // Newest first, exactly the hub's three denials for this agent (not run_assistant's allowed call).
  await expect(rows(page, "Recent denials")).toHaveCount(3);
  await expect(rows(page, "Recent denials").locator("td:nth-child(2)")).toHaveText([
    "tool:mocks.provision_laptop",
    "tool:mongo.update_one/new_hires",
    "tool:mongo.find/other",
  ]);
  await expect(rows(page, "Recent denials").first()).toContainText("server_not_in_manifest");
  expect(auditQueries.map((q) => Object.fromEntries(q))).toContainEqual({
    action: "mcp.tool_call",
    outcome: "denied",
    actor_type: "agent",
    actor_id: "db_agent",
    limit: "10",
  });
});

for (const email of ["u_acme_hr1@example.com", "u_acme_mgr_ravi@example.com"]) {
  test(`${email} is refused every admin view and every admin route`, async ({ page, request }) => {
    const adminCalls: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (ADMIN_ROUTES.some((route) => path.startsWith(`/api${route.split("/").slice(0, 2).join("/")}`))) adminCalls.push(path);
    });
    await signIn(page, email);
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Admin" })).toHaveCount(0);
    for (const path of ["/admin", "/admin/agents/db_agent", "/admin/audit", "/admin/pii-access", "/admin/evals"]) {
      await goWithin(page, path);
      await expect(page.getByRole("heading", { name: "Not available" })).toBeVisible();
    }
    expect(adminCalls).toEqual([]);

    // The API itself refuses the role, whatever the browser does.
    const headers = { Authorization: `Bearer ${await tokenFor(request, email)}` };
    for (const route of ADMIN_ROUTES) {
      const response = await request.get(`/api${route}`, { headers });
      expect(response.status(), route).toBe(403);
      expect(((await response.json()) as { error: { code: string } }).error.code).toBe("FORBIDDEN");
    }
  });
}
