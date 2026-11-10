/**
 * The signed-in session: the access token, held in memory only (docs/adr/0005-web-token-storage.md).
 * Nothing goes to localStorage, sessionStorage or a cookie, so a page reload signs the user out.
 */

export interface Session {
  readonly accessToken: string;
  /** Unix time in seconds, from the API's `expires_at`. */
  readonly expiresAt: number;
}

export interface SessionStore {
  get(): Session | null;
  set(session: Session): void;
  clear(): void;
  subscribe(listener: () => void): () => void;
}

export function createSessionStore(nowMs: () => number = Date.now): SessionStore {
  let current: Session | null = null;
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((listener) => listener());
  const isLive = (session: Session) => session.expiresAt * 1000 > nowMs();

  return {
    get() {
      if (current !== null && !isLive(current)) {
        current = null;
      }
      return current;
    },
    set(session) {
      current = isLive(session) ? Object.freeze({ ...session }) : null;
      notify();
    },
    clear() {
      current = null;
      notify();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
