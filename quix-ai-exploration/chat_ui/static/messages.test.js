import { describe, it, expect, beforeEach } from "vitest";
import { buildMessageEl, renderBatch, sortAscending } from "./messages.js";

describe("buildMessageEl", () => {
  it("renders user/assistant bubbles with role + content", () => {
    const el = buildMessageEl({ role: "User", content: "hello" });
    expect(el.className).toBe("msg user");
    expect(el.querySelector(".role").textContent).toBe("user");
    expect(el.querySelector(".body").textContent).toBe("hello");
  });

  it("renders non-synthetic system messages", () => {
    const el = buildMessageEl({
      role: "System",
      content: "you crossed 80%",
      synthetic: false,
    });
    expect(el.className).toBe("msg system");
    expect(el.textContent).toBe("you crossed 80%");
  });

  it("skips synthetic system messages (e.g. agent-selected)", () => {
    expect(
      buildMessageEl({ role: "System", content: "Agent selected: X", synthetic: true }),
    ).toBeNull();
  });

  it("skips Tool-role messages (rendered separately once we support tool blocks)", () => {
    expect(buildMessageEl({ role: "Tool", content: "..." })).toBeNull();
  });

  it("is xss-safe — content goes through textContent, never innerHTML", () => {
    const el = buildMessageEl({ role: "User", content: "<img src=x onerror=alert(1)>" });
    expect(el.querySelector(".body").textContent).toBe("<img src=x onerror=alert(1)>");
    expect(el.querySelector("img")).toBeNull();
  });
});

describe("renderBatch", () => {
  /** @type {HTMLElement} */
  let log;

  beforeEach(() => {
    document.body.innerHTML = "<div id='log'></div>";
    log = document.getElementById("log");
  });

  it("appends in order given", () => {
    renderBatch(log, [
      { role: "User", content: "one" },
      { role: "Assistant", content: "two" },
      { role: "User", content: "three" },
    ]);
    const bodies = [...log.querySelectorAll(".body")].map((e) => e.textContent);
    expect(bodies).toEqual(["one", "two", "three"]);
  });

  it("prepends a batch preserving the batch's internal order (regression)", () => {
    // Simulate: existing messages in log, then older page loaded via prepend.
    renderBatch(log, [
      { role: "User", content: "new-1" },
      { role: "Assistant", content: "new-2" },
    ]);

    // Older page comes back sorted ascending by sequenceNumber.
    renderBatch(
      log,
      [
        { role: "User", content: "old-1" },
        { role: "Assistant", content: "old-2" },
        { role: "User", content: "old-3" },
      ],
      { prepend: true },
    );

    const bodies = [...log.querySelectorAll(".body")].map((e) => e.textContent);
    // Must be: older-batch in order, then newer-batch in order.
    expect(bodies).toEqual(["old-1", "old-2", "old-3", "new-1", "new-2"]);
  });

  it("filters out synthetic + unknown roles from the batch", () => {
    renderBatch(log, [
      { role: "System", content: "agent-selected", synthetic: true },
      { role: "User", content: "u1" },
      { role: "Tool", content: "tool-result" },
      { role: "Assistant", content: "a1" },
    ]);
    const bodies = [...log.querySelectorAll(".body")].map((e) => e.textContent);
    expect(bodies).toEqual(["u1", "a1"]);
  });

  it("is a no-op for an all-filtered batch", () => {
    renderBatch(log, [
      { role: "System", content: "x", synthetic: true },
      { role: "Tool", content: "y" },
    ]);
    expect(log.children.length).toBe(0);
  });
});

describe("sortAscending", () => {
  it("sorts by sequenceNumber", () => {
    const sorted = sortAscending([
      { sequenceNumber: 3, content: "c" },
      { sequenceNumber: 1, content: "a" },
      { sequenceNumber: 2, content: "b" },
    ]);
    expect(sorted.map((m) => m.content)).toEqual(["a", "b", "c"]);
  });

  it("treats missing sequenceNumber as 0", () => {
    const sorted = sortAscending([
      { sequenceNumber: 2, content: "b" },
      { content: "zero" },
      { sequenceNumber: 1, content: "a" },
    ]);
    expect(sorted.map((m) => m.content)).toEqual(["zero", "a", "b"]);
  });

  it("does not mutate the input array", () => {
    const input = [{ sequenceNumber: 2 }, { sequenceNumber: 1 }];
    const snapshot = [...input];
    sortAscending(input);
    expect(input).toEqual(snapshot);
  });
});
