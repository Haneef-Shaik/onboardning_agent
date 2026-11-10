/**
 * Who is signed in. The token lives in the session store (memory only); the profile (`/me`, with the
 * stored role) is a TanStack Query query that runs while a session exists.
 */
import type { ApiClient, Me } from "@onb/ts-api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { fetchMe, login as loginRequest } from "../api";
import type { SessionStore } from "./session";

export const ME_QUERY_KEY = ["me"] as const;
export const SESSION_ENDED = "Your session has ended. Sign in again.";

export interface AuthState {
  status: "signed-out" | "loading" | "signed-in" | "error";
  me: Me | null;
  /** Why the user was signed out without asking, if they were. */
  notice: string | null;
  login(email: string, password: string): Promise<void>;
  logout(): void;
  retry(): void;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const state = use(AuthContext);
  if (state === null) throw new Error("useAuth needs an AuthProvider");
  return state;
}

/** Clears the session when its token expires, so the UI never keeps a dead token. */
function useExpiry(session: SessionStore, expiresAt: number | undefined) {
  useEffect(() => {
    if (expiresAt === undefined) return undefined;
    const expire = () => {
      // The timer is for this token; a newer session has its own timer.
      if (session.get()?.expiresAt === expiresAt || session.get() === null) session.clear();
    };
    const timer = setTimeout(expire, Math.max(0, expiresAt * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [session, expiresAt]);
}

export function AuthProvider({ client, session, children }: { client: ApiClient; session: SessionStore; children: ReactNode }) {
  const queryClient = useQueryClient();
  const current = useSyncExternalStore(session.subscribe, session.get);
  const [notice, setNotice] = useState<string | null>(null);
  const signingOut = useRef(false);
  const hadSession = useRef(false);

  useEffect(() => {
    if (current !== null) {
      hadSession.current = true;
      return;
    }
    // Nothing cached for one user may reach the next one signing in on this tab.
    queryClient.clear();
    if (hadSession.current && !signingOut.current) setNotice(SESSION_ENDED);
    hadSession.current = false;
    signingOut.current = false;
  }, [current, queryClient]);
  useExpiry(session, current?.expiresAt);

  // Keyed by the token too, so a profile can never be served to a different session.
  const me = useQuery({
    queryKey: [...ME_QUERY_KEY, current?.accessToken ?? null],
    queryFn: () => fetchMe(client),
    enabled: current !== null,
    staleTime: Infinity,
  });

  const login = useCallback(
    async (email: string, password: string) => {
      const next = await loginRequest(client, email, password);
      setNotice(null);
      session.set(next);
    },
    [client, session],
  );
  const logout = useCallback(() => {
    signingOut.current = true;
    setNotice(null);
    session.clear();
  }, [session]);
  const { refetch } = me;
  const retry = useCallback(() => void refetch(), [refetch]);

  const value = useMemo<AuthState>(() => {
    const status = current === null ? "signed-out" : me.isError ? "error" : me.data ? "signed-in" : "loading";
    return { status, me: current === null ? null : (me.data ?? null), notice, login, logout, retry };
  }, [current, me.isError, me.data, notice, login, logout, retry]);

  return <AuthContext value={value}>{children}</AuthContext>;
}
