import { QueryClient } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { createAppClient } from "./api";
import { App } from "./App";
import { createSessionStore } from "./auth/session";
import "./index.css";

const session = createSessionStore();
// Same origin, under /api: the Vite proxy in dev, Caddy behind https://localhost.
const client = createAppClient({ session, onUnauthenticated: () => undefined });
const queryClient = new QueryClient({
  defaultOptions: {
    // An API error is an answer, not a blip: retry only network failures, once.
    queries: { retry: (failures, error) => failures < 1 && !("code" in error) },
  },
});

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <App client={client} session={session} queryClient={queryClient} />
      </BrowserRouter>
    </StrictMode>,
  );
}
