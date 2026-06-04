import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisCard } from "@/app/analysis/ai-summary/components/analysis-card";
import type { Analysis } from "@/types/analysis";

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

  it("renders footer with model + tokens + duration", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/4218/)).toBeInTheDocument();
    expect(screen.getByText(/1132/)).toBeInTheDocument();
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
});
