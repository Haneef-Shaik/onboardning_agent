import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { aliases } from "./aliases";

// Dev server (PRD §7.11: dev profile, hot reload). `/api/*` goes to the API with the prefix
// stripped, as Caddy does (infra/caddy/Caddyfile.dev). Override the target with WEB_API_TARGET.
const apiTarget = process.env.WEB_API_TARGET ?? "http://127.0.0.1:8000";
const port = Number(process.env.WEB_PORT ?? 5173);
// Loopback by default. On Linux, Caddy reaches the host through the docker bridge: set WEB_HOST to
// that address, never 0.0.0.0 (the dev server would be on the LAN, past Caddy).
const host = process.env.WEB_HOST ?? "127.0.0.1";

export default defineConfig({
  root: import.meta.dirname,
  plugins: [react(), tailwindcss()],
  resolve: { alias: aliases },
  server: {
    host,
    port,
    strictPort: true,
    // Caddy forwards Host "localhost"; Vite refuses unknown hosts by default.
    allowedHosts: ["localhost"],
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  build: { outDir: "../../dist/apps/web", emptyOutDir: true },
});
