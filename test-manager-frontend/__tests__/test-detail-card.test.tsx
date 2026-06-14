import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { TestDetailCard } from "@/components/tests/test-detail-card";
import type { Test } from "@/types/test";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));
vi.mock("@/lib/hooks/use-api", () => ({
  useTestsApi: () => ({ getTelemetryParams: vi.fn() }),
}));
vi.mock("@/lib/hooks/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));
vi.mock("@/lib/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({ formatDateTime: (s: string) => s }),
}));

// A test with sessions on DIFFERENT tracks/cars — sessions[0] is Spa, the
// second is Monza. Analyzing the second must use ITS OWN track/car.
const TEST: Test = {
  test_id: "TST-0007",
  experiment_id: "EXP-1",
  pc_device_id: "DEV-1",
  test_rig_device_id: "DEV-2",
  environment_id: "ENV-1",
  driver: "Daniel",
  requirements: "",
  mode: "easy",
  sessions: [
    {
      session_id: "2026-06-03T11:08:18.206Z",
      track: "spa",
      car_model: "lambo",
    },
    {
      session_id: "2026-06-03T12:00:00.000Z",
      track: "monza",
      car_model: "ferrari",
    },
  ],
  pc_device_name: null,
  test_rig_device_name: null,
  environment_name: null,
  created_at: "2026-06-03T10:00:00Z",
  updated_at: "2026-06-03T10:00:00Z",
  config_id: "cfg-1",
  config_type: null,
  target_key: null,
  config_version: null,
};

function clickAnalyzeFor(sessionId: string) {
  const row = screen.getByText(sessionId).closest("div") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: /analyze/i }));
  return new URLSearchParams(
    (push.mock.calls.at(-1)![0] as string).split("?")[1],
  );
}

describe("TestDetailCard — per-session Analyze", () => {
  beforeEach(() => push.mockClear());

  it("deep-links to the exact session with that session's own track/car", () => {
    render(<TestDetailCard test={TEST} />);
    const qs = clickAnalyzeFor("2026-06-03T12:00:00.000Z");
    expect(qs.get("tab")).toBe("compare");
    expect(qs.get("test_id")).toBe("TST-0007");
    expect(qs.get("session_id")).toBe("2026-06-03T12:00:00.000Z");
    // The fix: Monza's own track/car, NOT sessions[0]'s (Spa/lambo).
    expect(qs.get("track")).toBe("monza");
    expect(qs.get("carModel")).toBe("ferrari");
  });

  it("uses the first session's own track/car for the first row", () => {
    render(<TestDetailCard test={TEST} />);
    const qs = clickAnalyzeFor("2026-06-03T11:08:18.206Z");
    expect(qs.get("session_id")).toBe("2026-06-03T11:08:18.206Z");
    expect(qs.get("track")).toBe("spa");
    expect(qs.get("carModel")).toBe("lambo");
  });
});

describe("TestDetailCard — View in Config Manager", () => {
  beforeEach(() => push.mockClear());

  it("navigates in-app to the Configurations page deep-linked to the config", () => {
    render(
      <TestDetailCard
        test={{ ...TEST, config_id: "cfg-1", config_version: 45 }}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /view in config manager/i }),
    );
    expect(push).toHaveBeenCalledWith(
      "/config-manager?config_id=cfg-1&config_version=45",
    );
  });

  it("falls back to /config-manager (no params) when the test has no config", () => {
    render(
      <TestDetailCard
        test={{ ...TEST, config_id: "", config_version: null }}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /view in config manager/i }),
    );
    expect(push).toHaveBeenCalledWith("/config-manager");
  });
});
