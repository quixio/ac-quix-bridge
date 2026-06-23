import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AnalysisCard } from "@/app/analysis/ai-summary/components/analysis-card";
import type { Analysis } from "@/types/analysis";

const { getPdf, getTelemetry, toast } = vi.hoisted(() => ({
  getPdf: vi.fn(),
  getTelemetry: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useAnalysesApi: () => ({ getPdf, getTelemetry }),
}));
vi.mock("@/lib/hooks/use-toast", () => ({
  useToast: () => ({ toast }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  getPdf.mockResolvedValue(new Blob(["%PDF-1.7"], { type: "application/pdf" }));
  getTelemetry.mockResolvedValue({ svg: null });
  // jsdom doesn't implement these:
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

function fullAnalysis(): Analysis {
  return {
    id: "aid",
    schema_version: 1,
    test_id: "TST-1",
    session_id: "2026-05-21T14:32:00Z",
    status: "complete",
    created_at: "2026-05-21T15:01:18Z",
    updated_at: "2026-05-21T15:01:51Z",
    model: "claude-opus-4-7",
    tokens_in: 4218,
    tokens_out: 1132,
    duration_ms: 33327,
    quix_session_id: "qsess-abc",
    kpis: [
      { name: "best_lap", value: "1:45.321", unit: "lap" },
      { name: "top_speed", value: 213.4, unit: "km/h" },
    ],
    requirements_check: [
      { requirement: "Lap < 1:46", met: true, evidence: "best 1:45.3" },
      { requirement: "Tyres < 95C", met: false, evidence: "RR=102C lap 8" },
      { requirement: "No off-track", met: null, evidence: "needs feedback" },
    ],
    logbook_refs: ["lb-1"],
    anomalies: [
      { severity: "warn", kind: "brake_spike", lap: 7, description: "FR 612C" },
    ],
    summary_md: "## Pace\n\nGreat session.",
    extra: {},
    activity: [],
  };
}

describe("AnalysisCard", () => {
  it("renders KPI tiles", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/best_lap/)).toBeInTheDocument();
    expect(screen.getByText(/1:45.321/)).toBeInTheDocument();
    expect(screen.getByText(/213.4/)).toBeInTheDocument();
  });

  it("renders requirements pills with tri-state styling", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/Lap < 1:46/)).toBeInTheDocument();
    expect(screen.getByText(/Tyres < 95C/)).toBeInTheDocument();
    expect(screen.getByText(/No off-track/)).toBeInTheDocument();
  });

  it("renders anomaly description", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/FR 612C/)).toBeInTheDocument();
  });

  it("renders Markdown narrative", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/Great session/)).toBeInTheDocument();
  });

  it("renders footer with model + duration", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/Generated in/)).toBeInTheDocument();
    expect(screen.getByText(/33s/)).toBeInTheDocument();
  });

  it("handles empty kpis/anomalies gracefully", () => {
    const empty: Analysis = {
      ...fullAnalysis(),
      kpis: [],
      anomalies: [],
      requirements_check: [],
    };
    render(<AnalysisCard analysis={empty} />);
    expect(screen.getByText(/Great session/)).toBeInTheDocument();
  });

  // --- Download PDF button (F2-UI) ---

  it("shows the Download PDF button when complete", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(
      screen.getByRole("button", { name: /download pdf/i }),
    ).toBeInTheDocument();
  });

  it("hides the Download PDF button when not complete", () => {
    for (const status of ["running", "failed"] as const) {
      const { unmount } = render(
        <AnalysisCard analysis={{ ...fullAnalysis(), status }} />,
      );
      expect(
        screen.queryByRole("button", { name: /download pdf/i }),
      ).not.toBeInTheDocument();
      unmount();
    }
  });

  it("fetches the blob and triggers a download on click", async () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    render(<AnalysisCard analysis={fullAnalysis()} />);
    fireEvent.click(screen.getByRole("button", { name: /download pdf/i }));

    await waitFor(() => expect(getPdf).toHaveBeenCalledWith("aid"));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(URL.createObjectURL).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it("toasts a destructive error when the PDF fetch fails", async () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    getPdf.mockRejectedValueOnce(new Error("boom"));
    render(<AnalysisCard analysis={fullAnalysis()} />);
    fireEvent.click(screen.getByRole("button", { name: /download pdf/i }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ variant: "destructive" }),
      ),
    );
    expect(clickSpy).not.toHaveBeenCalled();
    clickSpy.mockRestore();
  });
});
