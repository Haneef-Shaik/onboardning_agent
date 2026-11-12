/**
 * Holds revealed sensitive values in component state only (never the query cache), each for the
 * `expires_in_seconds` the API names (FR-P2), and drops them on unmount.
 */
import type { PiiField, RevealedValue } from "@onb/ts-api-client";
import { useCallback, useEffect, useRef, useState } from "react";

export type RevealFn = (field: PiiField, reason: string) => Promise<RevealedValue>;
export type RevealedValues = Partial<Record<PiiField, string>>;

export interface UseReveal {
  values: RevealedValues;
  reveal(field: PiiField, reason: string): Promise<void>;
  hide(field: PiiField): void;
  clear(): void;
}

export function useReveal(revealFn: RevealFn): UseReveal {
  const [values, setValues] = useState<RevealedValues>({});
  const timers = useRef(new Map<PiiField, ReturnType<typeof setTimeout>>());
  // One ticket per reveal in flight. hide, clear and unmount invalidate it, so a late answer is dropped.
  const tickets = useRef(new Map<PiiField, symbol>());

  const stopTimer = useCallback((field: PiiField) => {
    const timer = timers.current.get(field);
    if (timer !== undefined) clearTimeout(timer);
    timers.current.delete(field);
  }, []);

  const hide = useCallback(
    (field: PiiField) => {
      stopTimer(field);
      tickets.current.delete(field);
      setValues((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== field)));
    },
    [stopTimer],
  );

  const clear = useCallback(() => {
    timers.current.forEach((timer) => clearTimeout(timer));
    timers.current.clear();
    tickets.current.clear();
    setValues({});
  }, []);

  const reveal = useCallback(
    async (field: PiiField, reason: string) => {
      const ticket = Symbol(field);
      tickets.current.set(field, ticket);
      const answer = await revealFn(field, reason);
      if (tickets.current.get(field) !== ticket) return; // hidden, cleared or unmounted meanwhile
      stopTimer(field);
      setValues((current) => ({ ...current, [field]: answer.value }));
      timers.current.set(
        field,
        setTimeout(() => hide(field), answer.expires_in_seconds * 1000),
      );
    },
    [revealFn, stopTimer, hide],
  );

  useEffect(() => {
    const held = timers.current;
    const pending = tickets.current;
    return () => {
      held.forEach((timer) => clearTimeout(timer));
      held.clear();
      pending.clear();
    };
  }, []);

  return { values, reveal, hide, clear };
}
