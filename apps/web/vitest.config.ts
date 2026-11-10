import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { aliases } from "./aliases";

export default defineConfig({
  root: import.meta.dirname,
  plugins: [react()],
  resolve: { alias: aliases },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
