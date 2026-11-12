import type { RevealedValue } from "@onb/ts-api-client";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReveal } from "./useReveal";

const answer = (value: string, seconds = 30): RevealedValue => ({
  hire_id: "h_1",
  field: "mobile",
  value,
  access_id: "acc_1",
  expires_in_seconds: seconds,
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useReveal", () => {
  it("holds a revealed value, then drops it after the TTL the API names", async () => {
    const reveal = vi.fn().mockResolvedValue(answer("synthetic-value", 30));
    const { result } = renderHook(() => useReveal(reveal));

    await act(() => result.current.reveal("mobile", "onboarding call"));
    expect(reveal).toHaveBeenCalledWith("mobile", "onboarding call");
    expect(result.current.values.mobile).toBe("synthetic-value");

    act(() => vi.advanceTimersByTime(29_999));
    expect(result.current.values.mobile).toBe("synthetic-value");
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.values.mobile).toBeUndefined();
  });

  it("hides a value on request and its timer no longer matters", async () => {
    const reveal = vi.fn().mockResolvedValue(answer("synthetic-value"));
    const { result } = renderHook(() => useReveal(reveal));
    await act(() => result.current.reveal("mobile", "reason"));

    act(() => result.current.hide("mobile"));

    expect(result.current.values.mobile).toBeUndefined();
  });

  it("restarts the clock when the same field is revealed again", async () => {
    const reveal = vi.fn().mockResolvedValueOnce(answer("first", 30)).mockResolvedValueOnce(answer("second", 30));
    const { result } = renderHook(() => useReveal(reveal));
    await act(() => result.current.reveal("mobile", "reason"));
    act(() => vi.advanceTimersByTime(20_000));
    await act(() => result.current.reveal("mobile", "reason again"));

    act(() => vi.advanceTimersByTime(20_000));
    expect(result.current.values.mobile).toBe("second");
    act(() => vi.advanceTimersByTime(10_000));
    expect(result.current.values.mobile).toBeUndefined();
  });

  it("keeps nothing when the reveal is refused, and passes the error on", async () => {
    const reveal = vi.fn().mockRejectedValue(new Error("refused"));
    const { result } = renderHook(() => useReveal(reveal));

    await expect(act(() => result.current.reveal("mobile", "reason"))).rejects.toThrow("refused");

    expect(result.current.values).toEqual({});
  });

  it("never keeps a value past unmount", async () => {
    const reveal = vi.fn().mockResolvedValue(answer("synthetic-value"));
    const { result, unmount } = renderHook(() => useReveal(reveal));
    await act(() => result.current.reveal("mobile", "reason"));

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it("hides every value at once on clear", async () => {
    const reveal = vi.fn().mockImplementation(async (field: string) => ({ ...answer(`v-${field}`), field }));
    const { result } = renderHook(() => useReveal(reveal));
    await act(() => result.current.reveal("mobile", "r"));
    await act(() => result.current.reveal("pan", "r"));

    act(() => result.current.clear());

    expect(result.current.values).toEqual({});
    expect(vi.getTimerCount()).toBe(0);
  });
});
