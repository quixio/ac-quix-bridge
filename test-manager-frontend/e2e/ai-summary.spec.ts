import { test, expect } from "@playwright/test";

/**
 * E2E for the AI Summary sub-tab.
 *
 * Mocks the backend at the network layer so the run is hermetic and the dev
 * stack only needs to serve the Next.js frontend. Walks picker → analyze →
 * polling → render.
 */
test.describe("AI Summary sub-tab", () => {
  test("picker → analyze → polling → render", async ({ page }) => {
    // GET /api/v1/tests (list)
    await page.route(/\/api\/v1\/tests(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        json: {
          items: [
            {
              test_id: "TST-1",
              driver: "Daniel",
              experiment_id: "exp-1",
              pc_device_id: "DEV-1",
              test_rig_device_id: "DEV-2",
              environment_id: "ENV-1",
              requirements: "",
              sessions: [],
              pc_device_name: null,
              test_rig_device_name: null,
              environment_name: null,
              created_at: "2026-05-21T00:00:00Z",
              updated_at: "2026-05-21T00:00:00Z",
              config_id: "cfg",
              config_type: "experiment",
              target_key: null,
              config_version: 1,
            },
          ],
          total: 1,
          page: 1,
          page_size: 200,
        },
      });
    });

    // GET /api/v1/tests/TST-1 (single)
    await page.route(/\/api\/v1\/tests\/TST-1$/, async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        json: {
          test_id: "TST-1",
          driver: "Daniel",
          experiment_id: "exp-1",
          pc_device_id: "DEV-1",
          test_rig_device_id: "DEV-2",
          environment_id: "ENV-1",
          requirements: "",
          sessions: [
            {
              session_id: "2026-05-21T14:32:00Z",
              track: "barcelona",
              car_model: "ferrari",
            },
          ],
          pc_device_name: null,
          test_rig_device_name: null,
          environment_name: null,
          created_at: "2026-05-21T00:00:00Z",
          updated_at: "2026-05-21T00:00:00Z",
          config_id: "cfg",
          config_type: "experiment",
          target_key: null,
          config_version: 1,
        },
      });
    });

    // Analyses list/create — GET returns empty until POST.
    let createdId: string | null = null;
    let pollCount = 0;
    await page.route(/\/api\/v1\/analyses(\?.*)?$/, async (route) => {
      const req = route.request();
      if (req.method() === "GET") {
        return route.fulfill({
          json: { items: [], total: 0, page: 1, page_size: 20 },
        });
      }
      if (req.method() === "POST") {
        createdId = "aid-1";
        return route.fulfill({
          status: 202,
          json: { analysis_id: createdId },
        });
      }
      return route.continue();
    });

    // Polling endpoint — first call returns running, then complete.
    await page.route(/\/api\/v1\/analyses\/aid-1$/, (route) => {
      pollCount++;
      const status = pollCount >= 2 ? "complete" : "running";
      route.fulfill({
        json: {
          id: "aid-1",
          schema_version: 1,
          test_id: "TST-1",
          session_id: "2026-05-21T14:32:00Z",
          status,
          created_at: "2026-05-21T15:00:00Z",
          updated_at: "2026-05-21T15:00:01Z",
          kpis:
            status === "complete"
              ? [{ name: "best_lap", value: "1:45.321" }]
              : [],
          requirements_check: [],
          logbook_refs: [],
          anomalies: [],
          summary_md: status === "complete" ? "Done." : "",
          extra: {},
          model: "claude-opus-4-7",
          tokens_in: 100,
          tokens_out: 50,
          duration_ms: 30000,
        },
      });
    });

    await page.goto("/analysis?tab=ai-summary&test_id=TST-1");

    // Session auto-selected by the picker (latest).
    await expect(page.getByRole("button", { name: /^Analyze$/ })).toBeVisible({
      timeout: 10_000,
    });
    await page.getByRole("button", { name: /^Analyze$/ }).click();

    // Poll → complete → KPI tile visible.
    await expect(page.getByText(/best_lap/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/1:45\.321/)).toBeVisible();
  });
});
