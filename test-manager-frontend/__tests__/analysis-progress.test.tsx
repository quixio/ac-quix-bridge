import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisProgress } from "@/app/analysis/ai-summary/components/analysis-progress";
import type { Analysis, ActivityEvent } from "@/types/analysis";

function analysisWith(
  activity: ActivityEvent[],
  status: Analysis["status"] = "analyzing",
): Analysis {
  return {
    id: "aid",
    schema_version: 2,
    test_id: "TST-1",
    session_id: "2026-05-21T14:32:00Z",
    status,
    created_at: "2026-05-21T15:01:18Z",
    updated_at: "2026-05-21T15:01:51Z",
    kpis: [],
    requirements_check: [],
    logbook_refs: [],
    anomalies: [],
    summary_md: "",
    extra: {},
    activity,
  };
}

describe("AnalysisProgress activity feed", () => {
  it("renders a tool call with its label and result", () => {
    render(
      <AnalysisProgress
        analysis={analysisWith([
          {
            ts: "2026-05-21T15:01:20Z",
            kind: "tool",
            tool: "run_query",
            label: "Querying lap times",
            result: "142 rows",
            error: false,
          },
        ])}
        fallbackStartedAt={0}
      />,
    );
    expect(screen.getByText("Querying lap times")).toBeInTheDocument();
    expect(screen.getByText("→ 142 rows")).toBeInTheDocument();
    expect(screen.getByText("✓")).toBeInTheDocument();
  });

  it("marks an errored tool result with ✗", () => {
    render(
      <AnalysisProgress
        analysis={analysisWith([
          {
            ts: "2026-05-21T15:01:20Z",
            kind: "tool",
            tool: "run_query",
            label: "Querying sectors",
            result: "query timeout",
            error: true,
          },
        ])}
        fallbackStartedAt={0}
      />,
    );
    expect(screen.getByText("✗")).toBeInTheDocument();
    expect(screen.getByText("→ query timeout")).toBeInTheDocument();
  });

  it("renders a grouped sandbox step as label + command detail", () => {
    render(
      <AnalysisProgress
        analysis={analysisWith([
          {
            ts: "2026-05-21T15:01:20Z",
            kind: "agent_start",
            label: "Analysis sandbox",
            result: "deep dive",
          },
          {
            ts: "2026-05-21T15:01:21Z",
            kind: "agent_step",
            sub: "command",
            label: "Run command",
            detail: 'echo "hi"',
          },
          {
            ts: "2026-05-21T15:01:22Z",
            kind: "agent_end",
            label: "Sandbox finished",
          },
        ])}
        fallbackStartedAt={0}
      />,
    );
    expect(screen.getByText("Analysis sandbox")).toBeInTheDocument();
    expect(screen.getByText("Run command")).toBeInTheDocument(); // displayName
    expect(screen.getByText("$")).toBeInTheDocument(); // command icon
    expect(screen.getByText('echo "hi"')).toBeInTheDocument(); // the detail
    expect(screen.getByText("Sandbox finished")).toBeInTheDocument();
  });

  it("pulses (no ✓/✗) on the trailing in-progress step", () => {
    const { container } = render(
      <AnalysisProgress
        analysis={analysisWith(
          [
            {
              ts: "2026-05-21T15:01:20Z",
              kind: "tool",
              tool: "run_query",
              label: "Querying lap times",
            },
          ],
          "analyzing",
        )}
        fallbackStartedAt={0}
      />,
    );
    const feed = container.querySelector('[aria-live="polite"]');
    expect(feed).not.toBeNull();
    expect(feed?.querySelector(".animate-pulse")).not.toBeNull();
    expect(feed?.textContent).not.toContain("✓");
    expect(feed?.textContent).not.toContain("✗");
  });

  it("shows the status verb and keeps the session ETA copy", () => {
    render(
      <AnalysisProgress analysis={analysisWith([])} fallbackStartedAt={0} />,
    );
    expect(screen.getByText(/Analyzing telemetry/)).toBeInTheDocument();
    expect(screen.getByText(/4–8 minutes/)).toBeInTheDocument();
  });
});
