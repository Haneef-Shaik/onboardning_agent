import { describe, expect, it, vi } from "vitest";
import { createSessionStore } from "./session";

const NOW_MS = 1_800_000_000_000;
const token = (expiresInS: number) => ({ accessToken: "tkn", expiresAt: NOW_MS / 1000 + expiresInS });

describe("createSessionStore", () => {
  it("starts empty", () => {
    expect(createSessionStore(() => NOW_MS).get()).toBeNull();
  });

  it("holds a token until it is cleared", () => {
    const store = createSessionStore(() => NOW_MS);
    store.set(token(60));
    expect(store.get()?.accessToken).toBe("tkn");
    store.clear();
    expect(store.get()).toBeNull();
  });

  it("treats an expired token as no session, and drops it", () => {
    let now = NOW_MS;
    const store = createSessionStore(() => now);
    store.set(token(60));
    now += 60_000;
    expect(store.get()).toBeNull();
    now -= 60_000;
    expect(store.get()).toBeNull();
  });

  it("refuses a token that is already expired", () => {
    const store = createSessionStore(() => NOW_MS);
    store.set(token(0));
    expect(store.get()).toBeNull();
  });

  it("tells subscribers when the session changes, until they unsubscribe", () => {
    const store = createSessionStore(() => NOW_MS);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set(token(60));
    store.clear();
    unsubscribe();
    store.set(token(60));
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("keeps the token in memory only: nothing is written to web storage", () => {
    const store = createSessionStore(() => NOW_MS);
    store.set(token(60));
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("hands out a copy, so a caller cannot change the stored session", () => {
    const store = createSessionStore(() => NOW_MS);
    store.set(token(60));
    const session = store.get();
    expect(Object.isFrozen(session)).toBe(true);
  });
});
