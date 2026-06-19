import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { AiSummaryTab } from "@/app/analysis/ai-summary/ai-summary-tab";

function stubLocalStorage() {
  const store: Record<string, string> = {};
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = v;
    },
    removeItem: (k: string) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
}

const {
  testsList,
  testsGet,
  analysesList,
  analysesCreate,
  analysesGet,
  toast,
  nav,
} = vi.hoisted(() => ({
  testsList: vi.fn(),
  testsGet: vi.fn(),
  analysesList: vi.fn(),
  analysesCreate: vi.fn(),
  analysesGet: vi.fn(),
  toast: vi.fn(),
  nav: { search: "test_id=TST-1&session_id=2026-05-22T10:30:00" },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: vi.fn() }),
}));
// Return STABLE references (the real hooks are useMemo'd) so the tab's effects,
// which list analysesApi/testsApi in their deps, don't re-fire every render.
vi.mock("@/lib/hooks/use-api", () => {
  const testsApi = { list: testsList, get: testsGet };
  const analysesApi = {
    list: analysesList,
    create: analysesCreate,
    get: analysesGet,
  };
  return {
    useTestsApi: () => testsApi,
    useAnalysesApi: () => analysesApi,
  };
});
vi.mock("@/lib/hooks/use-toast", () => ({ useToast: () => ({ toast }) }));
// Mock polling so isAnalyzing is driven purely by activeAnalysisId (set by the
// server-state discovery under test), not by timer-based fetches.
vi.mock("@/app/analysis/ai-summary/hooks/use-analysis-polling", () => ({
  useAnalysisPolling: () => ({ data: null, error: null }),
}));
// Stub heavy children — only the AnalyzeButton is under test here.
vi.mock("@/app/analysis/ai-summary/components/test-session-picker", () => ({
  TestSessionPicker: () => null,
}));
vi.mock("@/app/analysis/ai-summary/components/analysis-card", () => ({
  AnalysisCard: () => null,
}));
vi.mock("@/app/analysis/ai-summary/components/analysis-progress", () => ({
  AnalysisProgress: () => null,
}));

function analysis(over: Record<string, unknown>) {
  return {
    id: "aid",
    test_id: "TST-1",
    session_id: "2026-05-22T10:30:00",
    status: "running",
    // Fresh by default so in-progress fixtures aren't treated as stale.
    created_at: new Date(Date.now() - 60_000).toISOString(),
    summary_md: "",
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  stubLocalStorage();
  nav.search = "test_id=TST-1&session_id=2026-05-22T10:30:00";
  testsList.mockResolvedValue({ items: [{ test_id: "TST-1", driver: "Daniel" }] });
  testsGet.mockResolvedValue({
    sessions: [{ session_id: "2026-05-22T10:30:00", track: "x", car_model: "y" }],
  });
  analysesGet.mockResolvedValue(analysis({ id: "aid-run", status: "running" }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AiSummaryTab — server in-progress discovery", () => {
  it("disables Analyze and shows Analyzing when the server reports an in-progress run for this target", async () => {
    analysesList.mockResolvedValue({
      items: [analysis({ id: "aid-run", status: "running" })],
      total: 1,
      page: 1,
      page_size: 20,
    });

    render(<AiSummaryTab />);

    const btn = await screen.findByRole("button", { name: /analyz/i });
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn).toHaveTextContent(/analyzing/i);
  });

  it("ignores a stale in-progress run (older than the cutoff) — button stays enabled", async () => {
    analysesList.mockResolvedValue({
      items: [
        analysis({
          id: "stale-run",
          status: "running",
          created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        }),
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });

    render(<AiSummaryTab />);

    const btn = await screen.findByRole("button", { name: /analyz/i });
    await waitFor(() => expect(btn).toBeEnabled());
  });

  it("leaves Analyze enabled when only a completed run exists (nothing in progress)", async () => {
    analysesList.mockResolvedValue({
      items: [analysis({ id: "aid-done", status: "complete", summary_md: "ok" })],
      total: 1,
      page: 1,
      page_size: 20,
    });

    render(<AiSummaryTab />);

    const btn = await screen.findByRole("button", { name: /analyz/i });
    await waitFor(() => expect(btn).toBeEnabled());
  });

  it("discovers an in-progress test-wide run (sessionIdIsNull) and disables", async () => {
    localStorage.setItem("analysis-mode", "test-wide");
    nav.search = "test_id=TST-1"; // test-wide needs no session
    analysesList.mockResolvedValue({
      items: [analysis({ id: "tw-run", session_id: null, status: "running" })],
      total: 1,
      page: 1,
      page_size: 20,
    });

    render(<AiSummaryTab />);

    const btn = await screen.findByRole("button", { name: /analyz/i });
    await waitFor(() => expect(btn).toBeDisabled());
    expect(analysesList).toHaveBeenCalledWith(
      expect.objectContaining({ sessionIdIsNull: true }),
    );
  });

  it("ignores a stale in-flight fetch after the target changes (no foreign active id)", async () => {
    // First target's history fetch stays pending; we resolve it late.
    let resolveStale!: (v: unknown) => void;
    const stale = new Promise((r) => {
      resolveStale = r;
    });
    analysesList.mockReturnValueOnce(stale);
    analysesList.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });

    const { rerender } = render(<AiSummaryTab />);
    await waitFor(() => expect(analysesList).toHaveBeenCalledTimes(1));

    // Navigate to a different session before the first fetch resolves.
    nav.search = "test_id=TST-1&session_id=2099-01-01T00:00:00";
    rerender(<AiSummaryTab />);
    await waitFor(() => expect(analysesList).toHaveBeenCalledTimes(2));

    // The OLD fetch now resolves with a running run — must be ignored.
    await act(async () => {
      resolveStale({
        items: [analysis({ id: "old-run", status: "running" })],
        total: 1,
        page: 1,
        page_size: 20,
      });
      await Promise.resolve();
    });

    const btn = await screen.findByRole("button", { name: /analyz/i });
    await waitFor(() => expect(btn).toBeEnabled());
  });

  it("keeps Test-wide mode when switching tests (mode is global/sticky)", async () => {
    localStorage.setItem("analysis-mode", "test-wide");
    nav.search = "test_id=TST-1";
    analysesList.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    });

    const { rerender } = render(<AiSummaryTab />);
    // Starts in Test-wide — the button reads "Analyze test".
    expect(
      await screen.findByRole("button", { name: /analyze test/i }),
    ).toBeInTheDocument();

    // Switch to a different test with no saved mode → must stay Test-wide.
    nav.search = "test_id=TST-2";
    rerender(<AiSummaryTab />);
    expect(
      await screen.findByRole("button", { name: /analyze test/i }),
    ).toBeInTheDocument();
  });
});
