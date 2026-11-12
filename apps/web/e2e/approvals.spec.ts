/**
 * UI-6: the Approvals Inbox and the mock Telegram outbox (PRD FR-H2, FR-N2, U5).
 *
 * Each test starts its own run of the engineer template for the seeded hire (Meera Iyer, Ravi's report) through
 * `/e2e/approval-runs`. The run is driven by the real orchestrator consumers until `manager_approval` waits for
 * Ravi; the decision then resumes it through the real `approval.decided` → `orchestrator.resume_approval` path,
 * and an outbox press through the real webhook → `decision.received` → `orchestrator.record_decision` path
 * (`e2e/approval_seed.py`). The browser never holds the webhook secret.
 */
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { openSection, signInAs } from "./helpers";

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? "18731"}`;

interface Started {
  run_id: string;
  approval_id: string | null;
  run_status: string;
}

async function startRun(request: APIRequestContext): Promise<{ runId: string; approvalId: string }> {
  const response = await request.post(`${API}/e2e/approval-runs`);
  expect(response.ok(), await response.text()).toBe(true);
  const started = (await response.json()) as Started;
  expect(started.run_status).toBe("running");
  if (!started.approval_id) throw new Error("the run did not wait for an approval");
  return { runId: started.run_id, approvalId: started.approval_id };
}

async function webhookCalls(request: APIRequestContext): Promise<number> {
  return ((await (await request.get(`${API}/e2e/webhook-calls`)).json()) as { telegram: number }).telegram;
}

/** The outbox messages whose press the webhook handler turned into `decision.received` (server-side record). */
async function webhookDecisions(request: APIRequestContext): Promise<string[]> {
  return ((await (await request.get(`${API}/e2e/webhook-decisions`)).json()) as { outbox_ids: string[] }).outbox_ids;
}

async function openRun(page: Page, runId: string) {
  await openSection(page, "Runs");
  await page.getByRole("link", { name: runId }).click();
  await expect(page.getByRole("heading", { level: 1, name: `Run ${runId}` })).toBeVisible();
}

async function openOutbox(page: Page) {
  await openSection(page, "Approvals");
  await page.getByRole("navigation", { name: "Approvals views" }).getByRole("link", { name: "Mock Telegram outbox" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Mock Telegram outbox" })).toBeVisible();
}

/** The outbox message for one run (each test's run has its own). */
function outboxMessage(page: Page, runId: string) {
  return page.locator('[data-testid^="outbox-"]').filter({ has: page.getByRole("link", { name: runId }) });
}

async function expectResumed(page: Page, runId: string) {
  await openRun(page, runId);
  await expect(page.getByTestId("task-manager_approval")).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("task-laptop")).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "completed");
}

async function managerContext(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signInAs(page, "MANAGER");
  return { context, page };
}

test("approving from the inbox resumes the run", async ({ page, request }) => {
  const { runId, approvalId } = await startRun(request);
  await signInAs(page, "MANAGER");
  await openRun(page, runId);
  await expect(page.getByTestId("task-manager_approval")).toHaveAttribute("data-status", "waiting_approval");
  await expect(page.getByTestId("task-laptop")).toHaveAttribute("data-status", "pending");

  await openSection(page, "Approvals");
  const card = page.getByTestId(`approval-${approvalId}`);
  await expect(card.getByTestId("approval-status")).toHaveAttribute("data-status", "pending");
  await expect(card).toContainText("Standard engineer kit"); // the packet, shown before deciding (U5)
  await card.getByLabel(/Comment/).fill("Fine for Meera");
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card.getByRole("status")).toContainText("Decision recorded");
  await expect(card.getByTestId("approval-decision")).toContainText("via web");

  await expectResumed(page, runId);
});

test("the mock outbox's Approve button calls POST /webhooks/telegram and resumes the run", async ({ page, request }) => {
  const { runId, approvalId } = await startRun(request);
  await signInAs(page, "MANAGER");
  const browserCalls: string[] = [];
  page.on("request", (sent) => browserCalls.push(`${sent.method()} ${new URL(sent.url()).pathname}`));
  const before = await webhookCalls(request);

  await openOutbox(page);
  const message = outboxMessage(page, runId);
  await expect(message).toContainText("Approval needed: Manager approves equipment & access");
  const outboxId = ((await message.getAttribute("data-testid")) ?? "").replace(/^outbox-/, "");
  expect(await webhookDecisions(request)).not.toContain(outboxId);
  const pressed = page.waitForRequest((sent) => sent.method() === "POST" && sent.url().includes("/press"));
  await message.getByRole("button", { name: "Approve" }).click();
  expect(new URL((await pressed).url()).pathname).toMatch(/^\/api\/mock-outbox\/[^/]+\/press$/);
  await expect(message.getByRole("status")).toContainText("Sent to the Telegram webhook");

  // The API called the webhook for this message (recorded at the server), the browser never did.
  expect(await webhookCalls(request)).toBeGreaterThan(before);
  expect(await webhookDecisions(request)).toContain(outboxId);
  expect(browserCalls.filter((call) => call.includes("/webhooks/"))).toEqual([]);

  // The decision arrived through Telegram's path: recorded with channel "telegram", and the run went on.
  await openSection(page, "Approvals");
  await expect(page.getByTestId(`approval-${approvalId}`).getByTestId("approval-decision")).toContainText(
    "via telegram",
  );
  await expectResumed(page, runId);
});

test('a second decision shows "already decided"', async ({ page, browser, request }) => {
  const { runId, approvalId } = await startRun(request);
  await signInAs(page, "MANAGER");

  // This page holds a view taken while the approval was pending: the inbox (no longer refreshed) and the outbox.
  await openSection(page, "Approvals");
  const staleCard = page.getByTestId(`approval-${approvalId}`);
  await expect(staleCard.getByRole("button", { name: "Approve" })).toBeVisible();
  await page.route("**/api/approvals?*", (route) => route.abort());

  // The first decision, from another session.
  const other = await managerContext(browser);
  try {
    await openSection(other.page, "Approvals");
    const card = other.page.getByTestId(`approval-${approvalId}`);
    await card.getByRole("button", { name: "Reject" }).click();
    await expect(card.getByRole("status")).toContainText("Decision recorded");
  } finally {
    await other.context.close();
  }

  // A second decision from the stale inbox.
  await staleCard.getByRole("button", { name: "Approve" }).click();
  await expect(staleCard.getByRole("alert").filter({ hasText: /already decided/i })).toBeVisible();

  // And from the outbox's buttons.
  await page.unroute("**/api/approvals?*");
  await openOutbox(page);
  const message = outboxMessage(page, runId);
  await message.getByRole("button", { name: "Approve" }).click();
  await expect(message.getByRole("alert")).toContainText(/already decided/i);

  // The first decision stands: a rejection fails the task, and the run with it (ADR 0020).
  await openRun(page, runId);
  await expect(page.getByTestId("task-manager_approval")).toHaveAttribute("data-status", "failed");
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "failed");
});
