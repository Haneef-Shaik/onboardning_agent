/**
 * Playwright for the web app (UI-1). `nx run web:e2e` starts the real API (`e2e/serve_api.py`) and
 * the Vite dev server, which proxies `/api` to it, then runs `e2e/`.
 *
 * Set E2E_BASE_URL to test through Caddy instead, e.g. https://localhost:<CADDY_HTTPS_PORT> after
 * `make up`, with API_DEV_UPSTREAM and WEB_DEV_UPSTREAM in `.env` pointing at the E2E ports.
 */
import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const apiPort = process.env.E2E_API_PORT ?? "18731";
const webPort = process.env.E2E_WEB_PORT ?? "18732";
// Made up per run and shared with the workers through the environment; never committed.
process.env.E2E_PASSWORD ??= randomBytes(18).toString("base64url");
process.env.E2E_JWT_SECRET ??= randomBytes(48).toString("base64url");

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../../dist/playwright/web",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? `http://127.0.0.1:${webPort}`,
    ignoreHTTPSErrors: true, // Caddy's internal CA in dev
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "uv run --frozen python apps/web/e2e/serve_api.py",
      cwd: repoRoot,
      url: `http://127.0.0.1:${apiPort}/health`,
      env: {
        E2E_API_PORT: apiPort,
        E2E_PASSWORD: process.env.E2E_PASSWORD,
        E2E_JWT_SECRET: process.env.E2E_JWT_SECRET,
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "node_modules/.bin/vite --config apps/web/vite.config.ts",
      cwd: repoRoot,
      url: `http://127.0.0.1:${webPort}/`,
      env: { WEB_PORT: webPort, WEB_API_TARGET: `http://127.0.0.1:${apiPort}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
