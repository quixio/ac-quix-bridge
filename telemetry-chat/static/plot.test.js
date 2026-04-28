import { describe, expect, it } from "vitest";
import { buildTraces, downsample, TRACE_COLORS } from "./plot.js";

describe("downsample", () => {
  it("returns input unchanged when below the cap", () => {
    const x = [0, 1, 2];
    const y = [10, 20, 30];
    const out = downsample(x, y, 1500);
    expect(out.x).toEqual(x);
    expect(out.y).toEqual(y);
  });

  it("reduces to at most maxPoints via stride sampling", () => {
    const x = Array.from({ length: 10000 }, (_, i) => i);
    const y = x.map((v) => v * 2);
    const out = downsample(x, y, 100);
    expect(out.x).toHaveLength(100);
    expect(out.y).toHaveLength(100);
    expect(out.x[0]).toBe(0);
    expect(out.y[0]).toBe(0);
    // Last sample should be near the end, not the very last index –
    // stride math picks round(99 * 100) = index 9900.
    expect(out.x[99]).toBeGreaterThan(9800);
  });

  it("handles empty or null inputs without crashing", () => {
    expect(downsample([], [], 100)).toEqual({ x: [], y: [] });
    // @ts-ignore – testing loose-contract handling
    expect(downsample(null, null, 100)).toEqual({ x: [], y: [] });
  });
});

describe("buildTraces", () => {
  const makeTrace = (sid, lap, extra = {}) => ({
    session_id: sid,
    lap,
    x: [0, 0.5, 1],
    y: [10, 20, 30],
    count: 3,
    ...extra,
  });

  it("labels single-session traces with just L{lap}", () => {
    const traces = buildTraces([
      makeTrace("s1", 1, { driver: "daniel" }),
      makeTrace("s1", 2, { driver: "daniel" }),
    ]);
    expect(traces[0].name).toBe("L1 daniel");
    expect(traces[1].name).toBe("L2 daniel");
  });

  it("prefixes S{n} when multiple sessions are present", () => {
    const traces = buildTraces([
      makeTrace("s1", 1, { driver: "ludvik" }),
      makeTrace("s2", 1, { driver: "tomas" }),
    ]);
    expect(traces[0].name).toBe("S1-L1 ludvik");
    expect(traces[1].name).toBe("S2-L1 tomas");
  });

  it("cycles colours deterministically from the palette", () => {
    const traces = buildTraces([makeTrace("s1", 1), makeTrace("s1", 2), makeTrace("s1", 3)]);
    expect(traces[0].line.color).toBe(TRACE_COLORS[0]);
    expect(traces[1].line.color).toBe(TRACE_COLORS[1]);
    expect(traces[2].line.color).toBe(TRACE_COLORS[2]);
  });
});
