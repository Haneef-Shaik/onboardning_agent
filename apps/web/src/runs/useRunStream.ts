/**
 * Follows `GET /runs/{id}/stream` (ADR 0018 §6). Each task or run event tells the screen to re-read the
 * run, so what is shown is always what the API says; the event list is an activity log, not the state.
 * The stream replays the run's history on every connection, so the log restarts on each one. The server
 * ends a long stream with `end {reason: "timeout"}`: the hook reconnects at once. A dropped connection
 * is retried with a growing pause. `end` without a reason (the run finished) or a 401/403/404 stops it.
 */
import type { ApiClient } from "@onb/ts-api-client";
import { useEffect, useRef, useState } from "react";
import { readSse } from "./sse";

export type StreamState = "connecting" | "live" | "ended";

export interface ActivityEntry {
  readonly key: number;
  readonly kind: "task" | "run";
  readonly taskId: string | null;
  readonly from: string;
  readonly to: string;
  readonly at: string;
}

export interface RunStream {
  state: StreamState;
  activity: readonly ActivityEntry[];
}

export interface StreamOptions {
  onChange: () => void;
  /** Pause before the nth reconnect after a dropped connection (ms). */
  retryDelayMs?: (attempt: number) => number;
}

const MAX_ACTIVITY = 200;
const RECONNECT_PAUSE_MS = 250; // after the server ends a stream with reason "timeout"
const STOP_STATUSES = new Set([401, 403, 404]);

const defaultDelay = (attempt: number) => Math.min(1000 * 2 ** attempt, 15_000);

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export function useRunStream(client: ApiClient, runId: string, options: StreamOptions): RunStream {
  const [state, setState] = useState<StreamState>("connecting");
  const [activity, setActivity] = useState<readonly ActivityEntry[]>([]);
  const latest = useRef(options);
  latest.current = options;

  useEffect(() => {
    const abort = new AbortController();
    const { signal } = abort;
    let key = 0;

    async function follow(): Promise<void> {
      let failures = 0;
      while (!signal.aborted) {
        setState("connecting");
        setActivity([]);
        try {
          const { response, data } = await client.GET("/runs/{run_id}/stream", {
            params: { path: { run_id: runId } },
            parseAs: "stream",
            signal,
          });
          if (!response.ok || !data) {
            if (STOP_STATUSES.has(response.status)) return setState("ended");
            throw new Error(`stream answered ${response.status}`);
          }
          failures = 0;
          setState("live");
          let timedOut = false;
          for await (const message of readSse(data)) {
            const body = asRecord(message.data);
            if (message.event === "end") {
              latest.current.onChange();
              if (body.reason !== "timeout") return setState("ended");
              timedOut = true; // the server's stream lifetime is over: reconnect at once
              break;
            }
            if (message.event === "task" || message.event === "run") {
              const entry: ActivityEntry = {
                key: key++,
                kind: message.event,
                taskId: message.event === "task" ? str(body.task_id) : null,
                from: str(body.from),
                to: str(body.to),
                at: str(body.at),
              };
              setActivity((current) => [...current, entry].slice(-MAX_ACTIVITY));
              latest.current.onChange();
            }
          }
          if (signal.aborted) return;
          if (!timedOut) throw new Error("stream closed without an end event"); // dropped: back off
          await sleep(RECONNECT_PAUSE_MS, signal); // never a hot loop if a server keeps timing streams out
        } catch {
          if (signal.aborted) return;
          failures += 1;
          await sleep((latest.current.retryDelayMs ?? defaultDelay)(failures - 1), signal);
        }
      }
    }

    void follow();
    return () => abort.abort();
  }, [client, runId]);

  return { state, activity };
}
