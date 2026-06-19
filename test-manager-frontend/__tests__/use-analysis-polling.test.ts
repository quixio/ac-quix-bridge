import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useAnalysisPolling } from "@/app/analysis/ai-summary/hooks/use-analysis-polling";
import type { Analysis } from "@/types/analysis";

function mockAnalysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: "aid",
    schema_version: 1,
    test_id: "t",
    session_id: "s",
    status: "pending",
    created_at: "2026-05-21T14:32:00Z",
    updated_at: "2026-05-21T14:32:00Z",
    kpis: [],
    requirements_check: [],
    logbook_refs: [],
    anomalies: [],
    summary_md: "",
    extra: {},
    activity: [],
    ...overrides,
  };
}

// Drain microtasks: settle a chain of resolved promises so React state updates
// triggered by `await fetcher(...)` apply before the next assertion.
async function flush(): Promise<void> {
  // Multiple ticks needed because the polling loop awaits fetcher, then setData,
  // then schedules the next setTimeout — each crosses a microtask boundary.
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

describe("useAnalysisPolling", () => {
  beforeEach(() =>
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout"] }),
  );
  afterEach(() => vi.useRealTimers());

  it("does not poll when analysisId is null", async () => {
    const fetcher = vi.fn();
    renderHook(() => useAnalysisPolling(null, fetcher));
    await flush();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("fetches immediately when analysisId is set", async () => {
    const fetcher = vi.fn().mockResolvedValue(mockAnalysis());
    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  });

  it("clears data when analysisId goes back to null", async () => {
    // Otherwise the last analysis lingers and the caller shows a stale report
    // for a different target after switching test/session.
    const fetcher = vi.fn().mockResolvedValue(mockAnalysis({ status: "complete" }));
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useAnalysisPolling(id, fetcher),
      { initialProps: { id: "aid" as string | null } },
    );
    await waitFor(() => expect(result.current.data).not.toBeNull());

    rerender({ id: null });
    await act(async () => {
      await flush();
    });
    expect(result.current.data).toBeNull();
  });

  it("stops polling on terminal status: complete", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(mockAnalysis({ status: "running" }))
      .mockResolvedValueOnce(mockAnalysis({ status: "complete" }))
      .mockResolvedValue(mockAnalysis({ status: "complete" }));

    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
      await flush();
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    // Past terminal — no more calls
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
      await flush();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("stops polling on terminal status: failed", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(mockAnalysis({ status: "failed", error: "x" }));
    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
      await flush();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("caps polls at MAX_POLLS (140) and then stops", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(mockAnalysis({ status: "running" }));
    renderHook(() => useAnalysisPolling("aid", fetcher));

    // Advance well past the cap. 5s steps cover both the 3s interval and the
    // post-60s backoff (5s), so each step triggers at most one poll.
    for (let i = 0; i < 170; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
        await flush();
      });
    }
    expect(fetcher.mock.calls.length).toBe(140);

    // Further time must not add polls — the cap holds.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
      await flush();
    });
    expect(fetcher.mock.calls.length).toBe(140);
  });
});
