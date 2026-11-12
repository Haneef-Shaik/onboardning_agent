import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAppClient } from "../api";
import { createSessionStore } from "../auth/session";
import { useRunStream } from "./useRunStream";

afterEach(cleanup);

const sse = (...blocks: string[]) =>
  new Response(blocks.join(""), { headers: { "content-type": "text/event-stream" } });
const task = (to: string) => `event: task\ndata: {"task_id":"a","from":"ready","to":"${to}","at":"t"}\n\n`;

function setup(answers: (() => Response)[]) {
  const seen: string[] = [];
  let next = 0;
  const handler = async (request: Request) => {
    seen.push(new URL(request.url).pathname);
    const answer = answers[Math.min(next++, answers.length - 1)];
    return answer ? answer() : new Response(null, { status: 500 });
  };
  const client = createAppClient({
    session: createSessionStore(),
    onUnauthenticated: () => undefined,
    baseUrl: "http://web.test/api",
    fetch: handler,
  });
  const onChange = vi.fn();
  const hook = renderHook(() => useRunStream(client, "run_1", { onChange, retryDelayMs: () => 5 }));
  return { hook, seen, onChange };
}

describe("useRunStream", () => {
  it("logs each task event, asks for a re-read on each, and stops at the end of a finished run", async () => {
    const { hook, onChange, seen } = setup([() => sse(task("running"), task("done"), 'event: end\ndata: {"status":"completed"}\n\n')]);
    await waitFor(() => expect(hook.result.current.state).toBe("ended"));
    expect(hook.result.current.activity.map((e) => `${e.taskId}:${e.from}>${e.to}`)).toEqual(["a:ready>running", "a:ready>done"]);
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(seen).toEqual(["/api/runs/run_1/stream"]); // no reconnect
  });

  it("reconnects at once when the end carries reason timeout, and replays from a clean log", async () => {
    const { hook, seen } = setup([
      () => sse(task("running"), 'event: end\ndata: {"status":"running","reason":"timeout"}\n\n'),
      () => sse(task("running"), task("done"), 'event: end\ndata: {"status":"completed"}\n\n'),
    ]);
    await waitFor(() => expect(hook.result.current.state).toBe("ended"));
    expect(seen).toHaveLength(2);
    expect(hook.result.current.activity).toHaveLength(2); // the replay, not 3
  });

  it("retries after a dropped connection and after a server error", async () => {
    const { hook, seen } = setup([
      () => new Response(null, { status: 502 }),
      () => sse(task("running")), // closed without an end event
      () => sse('event: end\ndata: {"status":"completed"}\n\n'),
    ]);
    await waitFor(() => expect(hook.result.current.state).toBe("ended"));
    expect(seen).toHaveLength(3);
  });

  it("gives up on a run it may not see", async () => {
    const { hook, seen } = setup([() => new Response(null, { status: 404 })]);
    await waitFor(() => expect(hook.result.current.state).toBe("ended"));
    expect(seen).toHaveLength(1);
  });

  it("stops following when the screen goes away", async () => {
    const { hook, seen } = setup([() => new Response(new ReadableStream({ start() {} }), { headers: { "content-type": "text/event-stream" } })]);
    await waitFor(() => expect(hook.result.current.state).toBe("live"));
    hook.unmount();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(seen).toHaveLength(1);
  });
});
