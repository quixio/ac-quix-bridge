import { test, expect } from "@playwright/test";

/**
 * E2E for the logbook entry list — verifies the per-entry session badge
 * renders correctly. Test-wide entries show "Test-wide"; session-linked
 * entries show the truncated ISO timestamp.
 */
test("logbook list shows session badge per entry", async ({ page }) => {
  const testId = "TST-1";
  const sessionId = "2026-05-21T14:32:00.000Z";

  await page.route(`**/api/v1/tests/${testId}/full`, (route) => {
    route.fulfill({
      json: {
        test: {
          test_id: testId,
          driver: "Daniel",
          experiment_id: "exp",
          pc_device_id: "DEV-1",
          test_rig_device_id: "DEV-2",
          environment_id: "ENV-1",
          requirements: "",
          sessions: [
            { session_id: sessionId, track: "barcelona", car_model: "ferrari" },
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
        logbook: [
          {
            id: "lb-1",
            test_id: testId,
            session_id: sessionId,
            content: "tied",
            created_at: "2026-05-21T15:00:00Z",
          },
          {
            id: "lb-2",
            test_id: testId,
            session_id: null,
            content: "wide",
            created_at: "2026-05-21T15:00:01Z",
          },
        ],
      },
    });
  });

  await page.goto(`/tests/${testId}`);
  // Session-linked entry shows truncated ISO.
  await expect(page.getByText(/2026-05-21T14:32/)).toBeVisible({
    timeout: 10_000,
  });
  // Unlinked entry shows the Test-wide badge.
  await expect(page.getByText(/Test-wide/)).toBeVisible();
});
