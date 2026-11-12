import { describe, expect, it } from "vitest";
import { readSse, type SseMessage } from "./sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(body: ReadableStream<Uint8Array>): Promise<SseMessage[]> {
  const out: SseMessage[] = [];
  for await (const message of readSse(body)) out.push(message);
  return out;
}

describe("readSse", () => {
  it("reads events with their JSON data and skips comment lines", async () => {
    const messages = await collect(
      streamOf(': stream open\n\nevent: task\ndata: {"task_id":"a","from":"ready","to":"running"}\n\n', "event: end\ndata: {\"status\":\"completed\"}\n\n"),
    );
    expect(messages).toEqual([
      { event: "task", data: { task_id: "a", from: "ready", to: "running" } },
      { event: "end", data: { status: "completed" } },
    ]);
  });

  it("reassembles an event split across chunks, even in the middle of a character", async () => {
    const bytes = new TextEncoder().encode('event: run\ndata: {"note":"é"}\n\n');
    const cut = bytes.indexOf(0xc3) + 1; // between the two bytes of "é"
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    });
    expect(await collect(body)).toEqual([{ event: "run", data: { note: "é" } }]);
  });

  it("accepts CRLF line ends", async () => {
    expect(await collect(streamOf('event: end\r\ndata: {"status":"failed"}\r\n\r\n'))).toEqual([
      { event: "end", data: { status: "failed" } },
    ]);
  });

  it("drops an event whose data is not JSON, and an incomplete last event", async () => {
    expect(await collect(streamOf("event: task\ndata: nope\n\n", 'event: task\ndata: {"a":1}'))).toEqual([]);
  });
});
