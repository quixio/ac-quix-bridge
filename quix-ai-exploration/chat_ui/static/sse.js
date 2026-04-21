// Parse a growing SSE buffer into discrete events.
// Returns [events, leftoverBuffer]. Each event has {event, data} strings.
export function parseSSE(buffer) {
  const events = [];
  let idx;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const evt = { event: "message", data: "" };
    for (const line of raw.split("\n")) {
      if (line.startsWith("event: ")) evt.event = line.slice(7).trim();
      else if (line.startsWith("data: ")) evt.data += line.slice(6);
    }
    events.push(evt);
  }
  return [events, buffer];
}

// Stream SSE events from a fetch response body. Yields one event at a time.
export async function* streamSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const [events, rest] = parseSSE(buffer);
    buffer = rest;
    for (const evt of events) yield evt;
  }
}
