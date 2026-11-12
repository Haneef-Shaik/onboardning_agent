/**
 * UI-7: the run assistant on the run page. The API is real (chat service, proposals, confirm, the run
 * control); only the assistant is scripted (`e2e/chat_seed.py`), so no model is called. A question about a
 * run with a failed task is answered from that task; "retry" also proposes retrying it.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { signInAs } from "./helpers";

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? "18731"}`;

async function startBlockedRun(request: APIRequestContext): Promise<{ runId: string; failed: string; other: string }> {
  const started = await request.post(`${API}/e2e/runs`);
  expect(started.ok(), await started.text()).toBe(true);
  const { run_id: runId, tasks } = (await started.json()) as { run_id: string; tasks: string[] };
  const [failed, other] = tasks;
  if (!failed || !other) throw new Error("the template has fewer than two tasks");
  const moved = await request.post(`${API}/e2e/runs/${runId}/move`, {
    data: { task_id: failed, to: "failed", error: "sink_rejected" },
  });
  expect(moved.ok(), await moved.text()).toBe(true);
  return { runId, failed, other };
}

async function openRun(page: Page, runId: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Runs" }).click();
  await page.getByRole("link", { name: runId }).click();
  await expect(page.getByRole("heading", { level: 1, name: `Run ${runId}` })).toBeVisible();
}

async function ask(page: Page, text: string) {
  await page.getByLabel("Ask about this run").fill(text);
  await page.getByRole("button", { name: "Ask" }).click();
}

test("a question gets an answer whose citations link to tasks", async ({ page, request }) => {
  await signInAs(page, "HR");
  const { runId, failed, other } = await startBlockedRun(request);
  await openRun(page, runId);

  await ask(page, "why is this onboarding stuck?");
  const answer = page.getByTestId("chat-answer");
  await expect(answer).toContainText(`Task ${failed} failed`);

  const link = answer.getByRole("link", { name: failed });
  await expect(link).toHaveAttribute("href", `#task-${failed}`);
  await expect(answer.getByRole("link")).toHaveCount(1); // only the cited task, not every task
  await link.click();
  await expect(page.getByTestId(`task-${failed}`)).toBeFocused();
  await expect(page.getByTestId(`task-${other}`)).not.toBeFocused();
  // A question changes nothing.
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "blocked");
  await expect(page.getByTestId(`task-${failed}`)).toHaveAttribute("data-status", "failed");
});

test("a proposed action shows a confirm dialog and runs only on confirm", async ({ page, request }) => {
  await signInAs(page, "HR");
  const { runId, failed } = await startBlockedRun(request);
  await openRun(page, runId);
  const writes: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/retry$|\/chat\/confirm$/.test(r.url())) writes.push(new URL(r.url()).pathname);
  });

  await ask(page, "please retry it");
  await expect(page.getByTestId("chat-answer")).toContainText("I can retry it if you confirm");
  // Saying yes in words, or a proposal on screen, changes nothing.
  await ask(page, "yes, do it");
  await expect(page.getByTestId("chat-answer")).toHaveCount(2);
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "blocked");
  expect(writes).toEqual([]);

  // The dialog: Cancel sends nothing.
  await page.getByRole("button", { name: "Review proposed retry" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(`Retry task ${failed}?`);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId(`task-${failed}`)).toHaveAttribute("data-status", "failed");
  expect(writes).toEqual([]);

  // Confirm runs the retry, once.
  await page.getByRole("button", { name: "Review proposed retry" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm retry" }).click();
  await expect(page.getByTestId(`task-${failed}`)).not.toHaveAttribute("data-status", "failed");
  await expect(page.getByTestId("run-status")).not.toHaveAttribute("data-status", "blocked");
  await expect(page.getByText("Retry confirmed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Review proposed retry" })).toHaveCount(0);
  expect(writes).toEqual([`/api/runs/${runId}/chat/confirm`]); // one confirm, not the page's own retry route
});

test("a manager can ask about their report's run but is never offered the retry", async ({ page, request, browser }) => {
  await signInAs(page, "HR");
  const { runId, failed } = await startBlockedRun(request);

  const context = await browser.newContext();
  const manager = await context.newPage();
  await signInAs(manager, "MANAGER");
  await openRun(manager, runId);
  await ask(manager, "retry it");
  await expect(manager.getByTestId("chat-answer")).toContainText(`Task ${failed} failed`);
  await expect(manager.getByRole("button", { name: "Review proposed retry" })).toHaveCount(0);
  await context.close();
});
