import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  root: import.meta.dirname,
  plugins: [react()],
  resolve: {
    alias: {
      "@onb/ts-ui": fileURLToPath(new URL("../../libs/ts-ui/src/index.ts", import.meta.url)),
      "@onb/ts-api-client": fileURLToPath(new URL("../../libs/ts-api-client/src/index.ts", import.meta.url)),
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "node",
  },
});
