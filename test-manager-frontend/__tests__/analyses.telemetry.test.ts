import { describe, expect, it, vi, beforeEach } from "vitest";
import { analysesApi } from "../lib/api/analyses";

describe("analysesApi.getTelemetry", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("GETs the telemetry endpoint and returns the svg payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: (_: string) => "application/json" },
      json: async () => ({ svg: "<svg/>" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await analysesApi.getTelemetry("a1", "tok");
    expect(fetchMock).toHaveBeenCalled();
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/analyses/a1/telemetry");
    expect(result).toEqual({ svg: "<svg/>" });
  });
});
