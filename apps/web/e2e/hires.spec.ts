/**
 * UI-4: hires with sensitive fields, masked by default, an audited reveal that hides again, and
 * document metadata. Real API (`serve_api.py`: the real hire and document services), synthetic data.
 */
import { expect, test } from "@playwright/test";
import { RAW, createHire, goInApp, openSection, signInAs, uniqueHire } from "./helpers";

const REVEAL_TTL_MS = 30_000;
const MASKED_FIELDS = ["Email", "Mobile", "Aadhaar", "PAN", "Bank account", "IFSC", "Account holder name"];

test("HR creates a hire, sees masked fields, reveals one with a reason, and it hides again after the TTL", async ({ page }) => {
  await page.clock.install();
  const hire = uniqueHire();
  await signInAs(page, "HR");

  await createHire(page, hire);

  // Masked everywhere by default: seven masked fields, none of the typed raw values on the page.
  const fields = page.getByTestId("masked-field");
  await expect(fields).toHaveCount(7);
  for (const label of MASKED_FIELDS) {
    await expect(page.getByTestId("masked-field").filter({ hasText: label })).toHaveAttribute("data-state", "masked");
  }
  const body = page.locator("body");
  for (const raw of [RAW.mobile, RAW.pan, RAW.bank_account, RAW.account_holder_name, "999900004202", "9000000042"]) {
    await expect(body).not.toContainText(raw);
  }
  await expect(body).not.toContainText(/ptk_/i);

  // A reveal needs a reason.
  const mobile = page.getByTestId("masked-field").filter({ hasText: "Mobile" });
  await page.getByRole("button", { name: "Reveal Mobile" }).click();
  await expect(page.getByRole("button", { name: "Confirm reveal" })).toBeDisabled();
  await page.getByLabel("Reason for revealing Mobile").fill("call about the joining date");
  await page.getByRole("button", { name: "Confirm reveal" }).click();

  await expect(mobile).toHaveAttribute("data-state", "revealed");
  await expect(mobile).toContainText(RAW.mobile);
  // Only that field was revealed.
  await expect(page.locator('[data-testid="masked-field"][data-state="revealed"]')).toHaveCount(1);

  // Still there just before the TTL, hidden at it.
  await page.clock.fastForward(REVEAL_TTL_MS - 2_000);
  await expect(mobile).toHaveAttribute("data-state", "revealed");
  await page.clock.fastForward(2_000);
  await expect(mobile).toHaveAttribute("data-state", "masked");
  await expect(body).not.toContainText(RAW.mobile);
});

test("a manager sees no reveal control; HR sees no Aadhaar reveal control", async ({ browser }) => {
  const hire = uniqueHire();

  const hrPage = await (await browser.newContext()).newPage();
  await signInAs(hrPage, "HR");
  await createHire(hrPage, hire);
  // HR: a control on every field but Aadhaar.
  await expect(hrPage.getByRole("button", { name: "Reveal Aadhaar" })).toHaveCount(0);
  await expect(hrPage.getByTestId("masked-field").filter({ hasText: "Aadhaar" })).toBeVisible();
  await expect(hrPage.getByRole("button", { name: /^Reveal / })).toHaveCount(6);
  await expect(hrPage.getByRole("button", { name: "Reveal Mobile" })).toBeVisible();

  // HR_ADMIN: the control exists for every field, Aadhaar too.
  const adminPage = await (await browser.newContext()).newPage();
  await signInAs(adminPage, "HR_ADMIN");
  await openSection(adminPage, "Hires");
  await adminPage.getByRole("link", { name: hire.name }).click();
  await expect(adminPage.getByRole("heading", { level: 1, name: hire.name })).toBeVisible();
  await expect(adminPage.getByRole("button", { name: /^Reveal / })).toHaveCount(7);
  await expect(adminPage.getByRole("button", { name: "Reveal Aadhaar" })).toBeVisible();

  // A manager has no Hires section, and no reveal control anywhere they can go.
  const managerPage = await (await browser.newContext()).newPage();
  await signInAs(managerPage, "MANAGER");
  await expect(managerPage.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Hires" })).toHaveCount(0);
  await goInApp(managerPage, "/hires");
  await expect(managerPage.getByRole("heading", { name: "Not available" })).toBeVisible();
  await goInApp(managerPage, `/hires/${new URL(adminPage.url()).pathname.split("/").pop()}`);
  await expect(managerPage.getByRole("heading", { name: "Not available" })).toBeVisible();
  await expect(managerPage.getByRole("button", { name: /^Reveal / })).toHaveCount(0);
  await expect(managerPage.getByTestId("masked-field")).toHaveCount(0);
});

test("a document upload shows its type, upload time and verified flag", async ({ page }) => {
  const hire = uniqueHire();
  await signInAs(page, "HR");
  await createHire(page, hire, { aadhaar: false });
  await expect(page.getByText("No documents yet.")).toBeVisible();

  const before = Date.now();
  await page.getByLabel("Document type").selectOption("pan");
  await page.getByLabel("Document file").setInputFiles({
    name: "scan-original-name.png",
    mimeType: "image/png",
    buffer: Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(256, 7)]),
  });
  await page.getByRole("button", { name: "Upload document" }).click();

  const table = page.getByRole("table", { name: "Documents" });
  const row = table.getByRole("row").nth(1);
  await expect(row).toContainText("PAN");
  await expect(row).toContainText("Not verified");
  await expect(row).toContainText("UTC");
  const uploadedAt = await row.locator("time").getAttribute("datetime");
  expect(Date.parse(uploadedAt ?? "")).toBeGreaterThan(before - 5_000);
  expect(Date.parse(uploadedAt ?? "")).toBeLessThan(Date.now() + 5_000);
  // Metadata only: the file name is never shown.
  await expect(page.locator("body")).not.toContainText("scan-original-name");

  // It survives navigating away and back (read from the API, not kept in the page).
  await openSection(page, "Hires");
  await page.getByRole("link", { name: hire.name }).click();
  await expect(page.getByRole("table", { name: "Documents" }).getByRole("row").nth(1)).toContainText("PAN");
});
