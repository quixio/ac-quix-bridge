import {
  test,
  expect,
  generateTestId,
  fillCreateTestForm,
  waitForToast,
} from "./fixtures";

/**
 * E2E test: Analyze button pre-checks DCM reachability.
 *
 * When DCM is unreachable, backend returns 503 on /telemetry-params. The UI
 * must surface a destructive toast and stay on the test detail page instead
 * of navigating to the Analysis tab where the user would see a confusing
 * fallback banner.
 */
test.describe("Analyze button guards against DCM outage", () => {
  test("shows toast and stays on detail page when DCM is unreachable", async ({
    page,
  }) => {
    // Create a throwaway test to analyze.
    await page.goto("/tests/add");
    const experimentId = generateTestId("e2e-dcm-down");
    await fillCreateTestForm(page, { experimentId });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/, { timeout: 10000 });
    const detailUrl = page.url();

    // Intercept telemetry-params for *any* test and simulate DCM-down.
    await page.route("**/api/v1/tests/*/telemetry-params", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "Configuration service unavailable: ConnectError",
        }),
      });
    });

    await page.getByRole("button", { name: "Analyze" }).click();

    // Toast appears with the expected message.
    await waitForToast(page, "Cannot open analysis");

    // URL hasn't changed — we're still on the test detail page.
    expect(page.url()).toBe(detailUrl);
  });
});
