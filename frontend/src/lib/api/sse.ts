/**
 * Minimal Server-Sent Events reader over `fetch` (EventSource cannot POST).
 * Yields `{ event, data }` pairs as they arrive.
 */
export interface SseMessage {
  event: string;
  data: string;
}

export async function* readSse(response: Response, signal?: AbortSignal): AsyncGenerator<SseMessage> {
  if (!response.body) throw new Error("Response has no body to stream.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const onAbort = () => reader.cancel().catch(() => undefined);
  signal?.addEventListener("abort", onAbort);

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const message = parseBlock(block);
        if (message) yield message;
        boundary = buffer.indexOf("\n\n");
      }
    }
    const tail = parseBlock(buffer);
    if (tail) yield tail;
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}

function parseBlock(block: string): SseMessage | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
