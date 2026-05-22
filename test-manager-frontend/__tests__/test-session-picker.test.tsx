import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TestSessionPicker } from "@/app/analysis/ai-summary/components/test-session-picker";

const TESTS = [
  { test_id: "TST-1", driver_name: "Daniel" },
  { test_id: "TST-2", driver_name: "Otta" },
];

const SESSIONS_BY_TEST: Record<
  string,
  Array<{ session_id: string; track: string; car_model: string }>
> = {
  "TST-1": [
    {
      session_id: "2026-05-21T14:32:00Z",
      track: "barcelona",
      car_model: "ferrari",
    },
    {
      session_id: "2026-05-21T12:00:00Z",
      track: "barcelona",
      car_model: "ferrari",
    },
  ],
  "TST-2": [],
};

describe("TestSessionPicker", () => {
  it("renders both dropdowns", () => {
    render(
      <TestSessionPicker
        tests={TESTS}
        sessionsByTest={SESSIONS_BY_TEST}
        selectedTestId={null}
        selectedSessionId={null}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/test/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/session/i)).toBeInTheDocument();
  });

  it("defaults session to latest session by ISO timestamp desc", () => {
    const onChange = vi.fn();
    render(
      <TestSessionPicker
        tests={TESTS}
        sessionsByTest={SESSIONS_BY_TEST}
        selectedTestId="TST-1"
        selectedSessionId={null}
        onChange={onChange}
      />,
    );
    // Auto-default fires on mount when sessions exist and selected is null
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "2026-05-21T14:32:00Z",
      }),
    );
  });

  it("shows 'no sessions yet' helper when test has zero sessions", () => {
    render(
      <TestSessionPicker
        tests={TESTS}
        sessionsByTest={SESSIONS_BY_TEST}
        selectedTestId="TST-2"
        selectedSessionId={null}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/no sessions on this test yet/i),
    ).toBeInTheDocument();
  });
});
