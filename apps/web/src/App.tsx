import type { ApiClient, Me } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import type { SessionStore } from "./auth/session";
import { canAccess, navFor, sectionOf } from "./nav/nav";
import { LoginPage } from "./pages/LoginPage";
import { NotAvailablePage, SectionPage } from "./pages/SectionPage";
import { Shell } from "./pages/Shell";

export interface AppProps {
  client: ApiClient;
  session: SessionStore;
  queryClient: QueryClient;
}

/** The app; the caller supplies the router (BrowserRouter in `main.tsx`, MemoryRouter in tests). */
export function App({ client, session, queryClient }: AppProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider client={client} session={session}>
        <AppRoutes />
      </AuthProvider>
    </QueryClientProvider>
  );
}

interface FromState {
  from?: string | undefined;
}

function homeOf(me: Me): string {
  return navFor(me.role)[0]?.path ?? "/";
}

function SignedOutRoutes() {
  const location = useLocation();
  if (location.pathname === "/login") return <LoginPage />;
  const from = location.pathname === "/" ? undefined : location.pathname;
  return <Navigate to="/login" replace state={{ from } satisfies FromState} />;
}

function AfterLogin({ me }: { me: Me }) {
  const from = (useLocation().state as FromState | null)?.from;
  // Only an in-app path; the guard below decides what it shows ("Not available" outside the role).
  const target = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : homeOf(me);
  return <Navigate to={target} replace />;
}

function Guarded({ me }: { me: Me }) {
  const { pathname } = useLocation();
  const item = sectionOf(pathname);
  return item && canAccess(me.role, pathname) ? <SectionPage item={item} /> : <NotAvailablePage />;
}

function AppRoutes() {
  const auth = useAuth();
  if (auth.status === "signed-out") return <SignedOutRoutes />;
  if (auth.status === "error") {
    return (
      <main className="p-6">
        <p role="alert">Could not load your profile.</p>
        <Button className="mt-2" onClick={auth.retry}>
          Try again
        </Button>
      </main>
    );
  }
  if (auth.me === null) return <p className="p-6">Loading…</p>;
  const me = auth.me;
  return (
    <Routes>
      <Route path="/login" element={<AfterLogin me={me} />} />
      <Route element={<Shell me={me} />}>
        <Route index element={<Navigate to={homeOf(me)} replace />} />
        <Route path="*" element={<Guarded me={me} />} />
      </Route>
    </Routes>
  );
}
