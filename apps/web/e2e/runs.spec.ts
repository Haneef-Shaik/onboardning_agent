/**
 * UI-5: the run board. The API is real (run service, store, SSE stream); no orchestrator runs in this
 * server, so each test plays its part through the test router (`e2e/run_seed.py`), moving tasks the way
 * the orchestrator does. Runs are started for the seeded hire (Meera Iyer, Ravi's report).
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { signInAs } from "./helpers";

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? "18731"}`;

interface Started {
  run_id: string;
  trace_id: string;
  tasks: string[];
}

/** A run for the seeded hire (not a new hire: UI-8's audit counts must hold while specs share a server). */
async function startRun(request: APIRequestContext): Promise<Started> {
  const response = await request.post(`${API}/e2e/runs`);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Started;
}

async function move(request: APIRequestContext, runId: string, taskId: string, to: string, error?: string) {
  const response = await request.post(`${API}/e2e/runs/${runId}/move`, { data: { task_id: taskId, to, error } });
  expect(response.ok(), await response.text()).toBe(true);
}

/** Signs in, starts a run, and opens the run's page through the nav. */
async function openNewRun(page: Page, request: APIRequestContext, role: "HR" | "HR_ADMIN" = "HR") {
  await signInAs(page, role);
  const started = await startRun(request);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Runs" }).click();
  await page.getByRole("link", { name: started.run_id }).click();
  await expect(page.getByRole("heading", { level: 1, name: `Run ${started.run_id}` })).toBeVisible();
  return started;
}

test("run detail updates live over SSE as tasks change", async ({ page, request }) => {
  const started = await openNewRun(page, request);
  const [first, second] = started.tasks;
  if (!first || !second) throw new Error("the template has fewer than two tasks");
  await expect(page.getByTestId("stream-state")).toHaveText("Live");
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "started");
  await expect(page.getByTestId(`task-${first}`)).toHaveAttribute("data-status", "pending");

  // No reload and no navigation between these steps: only the stream tells the page.
  let reloads = 0;
  page.on("framenavigated", () => (reloads += 1));
  await move(request, started.run_id, first, "running");
  await expect(page.getByTestId(`task-${first}`)).toHaveAttribute("data-status", "running");
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "running");
  await expect(page.getByTestId("activity")).toContainText(`${first}: ready → running`);

  await move(request, started.run_id, first, "done");
  await expect(page.getByTestId(`task-${first}`)).toHaveAttribute("data-status", "done");
  await move(request, started.run_id, second, "running");
  await expect(page.getByTestId(`task-${second}`)).toHaveAttribute("data-status", "running");
  expect(reloads).toBe(0);
});

test("a blocked task shows its reason; retry puts it back in play", async ({ page, request }) => {
  const started = await openNewRun(page, request);
  const [first, second] = started.tasks;
  if (!first || !second) throw new Error("the template has fewer than two tasks");
  await move(request, started.run_id, first, "failed", "sink_rejected");

  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "blocked");
  const row = page.getByTestId(`task-${first}`);
  await expect(row).toHaveAttribute("data-status", "failed");
  await expect(row.getByTestId("blocked-reason")).toContainText("rejected");
  await expect(page.getByTestId(`task-${second}`).getByTestId("blocked-reason")).toHaveCount(0);

  await row.getByRole("button", { name: "Retry" }).click();
  await expect(row).not.toHaveAttribute("data-status", "failed");
  await expect(row.getByTestId("blocked-reason")).toHaveCount(0);
  await expect(page.getByTestId("run-status")).not.toHaveAttribute("data-status", "blocked");
});

test("a blocked task is completed by hand only with a reason", async ({ page, request }) => {
  const started = await openNewRun(page, request, "HR_ADMIN");
  const [first] = started.tasks;
  if (!first) throw new Error("the template has no tasks");
  await move(request, started.run_id, first, "failed", "LeaseExpired");

  const row = page.getByTestId(`task-${first}`);
  await expect(row.getByTestId("blocked-reason")).toContainText("worker");
  await row.getByRole("button", { name: "Complete…" }).click();
  await expect(row.getByRole("button", { name: "Confirm complete" })).toBeDisabled();
  await row.getByLabel(/Reason for completing/).fill("collected on paper at the front desk");
  await row.getByRole("button", { name: "Confirm complete" }).click();

  await expect(row).toHaveAttribute("data-status", "done");
  await expect(row.getByTestId("blocked-reason")).toHaveCount(0);
});

test("the trace link opens Jaeger on the run's trace id", async ({ page, request, context }) => {
  const started = await openNewRun(page, request);
  const link = page.getByRole("link", { name: "Open trace" });
  await expect(link).toBeVisible();

  const expected = `https://jaeger.localhost/trace/${started.trace_id}`;
  await expect(link).toHaveAttribute("href", expected);

  // Jaeger is not part of this server: the new tab is stopped at the network, and its URL is the proof.
  await context.route("https://jaeger.localhost/**", (route) => route.fulfill({ status: 200, body: "jaeger" }));
  const popup = context.waitForEvent("page");
  await link.click();
  const opened = await popup;
  await opened.waitForLoadState();
  expect(opened.url()).toBe(expected);
  expect(new URL(opened.url()).pathname).toBe(`/trace/${started.trace_id}`);
});

test("a manager sees a run of their report, but no controls and no trace link", async ({ page, request, browser }) => {
  const started = await openNewRun(page, request);
  await move(request, started.run_id, started.tasks[0] ?? "", "failed", "sink_rejected");

  const context = await browser.newContext();
  const managerPage = await context.newPage();
  await signInAs(managerPage, "MANAGER");
  await managerPage.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Runs" }).click();
  // The hire is Ravi's report (the form sends his id), so the run is his to see.
  await managerPage.getByRole("link", { name: started.run_id }).click();
  await expect(managerPage.getByTestId("run-status")).toHaveAttribute("data-status", "blocked");
  await expect(managerPage.getByTestId("blocked-reason")).toBeVisible();
  await expect(managerPage.getByRole("button", { name: /Retry|Complete|Pause|Resume|Cancel run/ })).toHaveCount(0);
  await expect(managerPage.getByRole("link", { name: "Open trace" })).toHaveCount(0);
  await context.close();
});
