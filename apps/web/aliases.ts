import { fileURLToPath } from "node:url";

/** Workspace libraries by source path; shared by the Vite and Vitest configs (and tsconfig paths). */
export const aliases = {
  "@onb/ts-ui": fileURLToPath(new URL("../../libs/ts-ui/src/index.ts", import.meta.url)),
  "@onb/ts-api-client": fileURLToPath(new URL("../../libs/ts-api-client/src/index.ts", import.meta.url)),
};
