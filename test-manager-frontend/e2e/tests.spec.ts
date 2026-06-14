import {
  test,
  expect,
  generateTestId,
  waitForToast,
  fillCreateTestForm,
} from "./fixtures";

/**
 * E2E Tests for Test Manager Frontend - Phase 3
 *
 * Tests the following functionality:
 * - Tests list page with filtering
 * - Test creation with Device selection
 * - Test editing
 * - Test deletion
 * - Form validation
 */

test.describe("Tests Management", () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to tests page before each test
    await page.goto("/tests");
  });

  test("should display tests list page", async ({ page }) => {
    // Verify page title
    await expect(page.getByRole("heading", { name: "Tests" })).toBeVisible();

    // Verify description
    await expect(
      page.getByText("Manage test executions and view results"),
    ).toBeVisible();

    // Verify Add Test link exists
    await expect(page.getByRole("link", { name: "Add Test" })).toBeVisible();
  });

  test("should navigate to test detail page", async ({ page }) => {
    // Wait for table to load
    await page.waitForSelector("table", { timeout: 10000 });

    // Click on first test row (if exists)
    const firstRow = page.locator("table tbody tr").first();
    const rowCount = await page.locator("table tbody tr").count();

    if (rowCount > 0) {
      await firstRow.click();

      // Wait for navigation to complete
      await page.waitForURL(/\/tests\/.+/, { timeout: 10000 });

      // Verify we're on detail page
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // Look for link containing "Edit Test" text, being more flexible
      await expect(
        page.locator('a:has-text("Edit Test")').first(),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Delete Test" }),
      ).toBeVisible();
    }
  });

  test("should filter tests by status", async ({ page }) => {
    // Open status filter dropdown
    const statusFilter = page.getByRole("combobox", { name: /status/i });

    if (await statusFilter.isVisible()) {
      await statusFilter.click();

      // Select "Draft" status
      await page.getByRole("option", { name: "Draft" }).click();

      // Wait for table to update
      await page.waitForTimeout(1000);

      // Verify URL contains filter parameter
      expect(page.url()).toContain("status=draft");
    }
  });

  test("should navigate to create test page", async ({ page }) => {
    await page.getByRole("link", { name: "Add Test" }).click();
    await page.waitForURL("/tests/add", { timeout: 10000 });
    await expect(
      page.getByRole("heading", { name: "Create Test" }),
    ).toBeVisible();
  });

  test("submit button is disabled until required fields are filled", async ({
    page,
  }) => {
    await page.goto("/tests/add");
    await expect(
      page.getByRole("button", { name: "Create Test" }),
    ).toBeDisabled();
  });

  test("should create a new test successfully", async ({ page }) => {
    await page.goto("/tests/add");
    const experimentId = generateTestId("e2e-exp");

    await fillCreateTestForm(page, { experimentId });
    await page.getByRole("button", { name: "Create Test" }).click();

    // Backend assigns auto-generated TST-XXXX id.
    await page.waitForURL(/\/tests\/TST-\d+$/, { timeout: 10000 });
    await expect(page.getByText("Test execution details")).toBeVisible();
    await expect(page.getByText(experimentId)).toBeVisible();
  });

  test("should edit an existing test", async ({ page }) => {
    // Create a throwaway test so the run is self-contained.
    await page.goto("/tests/add");
    await fillCreateTestForm(page, {
      experimentId: generateTestId("e2e-edit"),
    });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/);

    await page.getByRole("link", { name: "Edit Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+\/edit$/);
    await expect(
      page.getByRole("heading", { name: /Edit Test:/ }),
    ).toBeVisible();

    // Save is gated on dirty state — typing requirements makes it enabled.
    await expect(page.getByTestId("save-test")).toBeDisabled();
    await page.locator("#requirements").fill("Updated by e2e");
    await expect(page.getByTestId("save-test")).toBeEnabled();
    await page.getByTestId("save-test").click();

    await page.waitForURL(/\/tests\/TST-\d+$/);
    await expect(page.getByText("Updated by e2e")).toBeVisible();
  });

  test("should delete a test with confirmation", async ({ page }) => {
    await page.goto("/tests/add");
    await fillCreateTestForm(page, { experimentId: generateTestId("e2e-del") });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/);

    await page.getByRole("button", { name: "Delete Test" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await expect(page.getByText(/permanently delete test/i)).toBeVisible();

    await page
      .getByRole("button", { name: "Delete", exact: true })
      .last()
      .click();

    await page.waitForURL("/tests", { timeout: 10000 });
    await expect(page.getByRole("heading", { name: "Tests" })).toBeVisible();
  });

  test("should cancel test creation", async ({ page }) => {
    await page.goto("/tests/add");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page).toHaveURL("/tests");
  });
});

test.describe("Tests Table Features", () => {
  test("should search tests by query", async ({ page }) => {
    await page.goto("/tests");

    // Find search input (use specific placeholder to avoid ambiguity)
    const searchInput = page.getByPlaceholder("Search tests...");

    if (await searchInput.isVisible()) {
      await searchInput.fill("test-123");

      // Wait for search to execute
      await page.waitForTimeout(1000);

      // Verify URL contains search parameter
      expect(page.url()).toContain("q=test-123");
    }
  });

  test("should clear all filters", async ({ page }) => {
    await page.goto("/tests?status=draft&campaign_id=test");

    // Click Clear Filters button
    const clearButton = page.getByRole("button", { name: /clear filters/i });

    if (await clearButton.isVisible()) {
      await clearButton.click();

      // Verify URL has no query parameters
      expect(page.url()).toBe(page.url().split("?")[0]);
    }
  });
});

test.describe("Entity CRUD smokes", () => {
  test("driver create and delete roundtrip", async ({ page }) => {
    const name = generateTestId("e2e-drv");
    await page.goto("/drivers/add");
    await page.locator("#name").fill(name);
    await page.getByRole("button", { name: "Create Driver" }).click();
    await page.waitForURL(/\/drivers\/DRV-\d+$/);
    await expect(page.getByRole("heading", { name })).toBeVisible();

    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page
      .getByRole("button", { name: "Delete", exact: true })
      .last()
      .click();
    await page.waitForURL("/drivers");
  });

  test("environment create and delete roundtrip", async ({ page }) => {
    const name = generateTestId("e2e-env");
    await page.goto("/environments/add");
    await page.locator("#name").fill(name);
    await page.locator("#location").fill("Brno");
    await page.getByRole("button", { name: "Create Environment" }).click();
    await page.waitForURL(/\/environments\/ENV-\d+$/);
    await expect(page.getByRole("heading", { name })).toBeVisible();

    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page
      .getByRole("button", { name: "Delete", exact: true })
      .last()
      .click();
    await page.waitForURL("/environments");
  });

  test("device create and delete roundtrip", async ({ page }) => {
    const name = generateTestId("e2e-dev");
    await page.goto("/devices/add");
    await page.locator("#name").fill(name);
    // Pick "PC" from the category dropdown.
    await page.getByRole("combobox").click();
    await page.getByRole("option", { name: /^PC$/ }).click();
    await page.getByRole("button", { name: "Create Device" }).click();
    await page.waitForURL(/\/devices\/DEV-\d+$/);
    await expect(page.getByRole("heading", { name })).toBeVisible();

    await page.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page
      .getByRole("button", { name: "Delete", exact: true })
      .last()
      .click();
    await page.waitForURL("/devices");
  });
});

test.describe("Tests pagination", () => {
  test("page_size=10 URL param limits rows and shows pagination controls", async ({
    page,
    request,
  }) => {
    // Ensure there are at least 11 tests so pagination kicks in.
    const API = "http://localhost:8080/api/v1";
    const existing = await request
      .get(`${API}/tests?page_size=100`)
      .then((r) => r.json());

    if (existing.total < 11) {
      // Seed the gap using the first live device/env/driver.
      const devs = await request
        .get(`${API}/devices?page_size=100`)
        .then((r) => r.json());
      const envs = await request
        .get(`${API}/environments?page_size=100`)
        .then((r) => r.json());
      const drvs = await request
        .get(`${API}/drivers?page_size=100`)
        .then((r) => r.json());
      const pc = devs.items.find((d: any) => d.category === "pc");
      const rig = devs.items.find((d: any) => d.category === "test_rig");
      const env = envs.items[0];
      const drv = drvs.items[0];
      expect(pc && rig && env && drv).toBeTruthy();

      const needed = 11 - existing.total;
      for (let i = 0; i < needed; i++) {
        await request.post(`${API}/tests`, {
          data: {
            experiment_id: `e2e-pagination-${Date.now()}-${i}`,
            pc_device_id: pc.device_id,
            test_rig_device_id: rig.device_id,
            environment_id: env.environment_id,
            driver: drv.name,
            requirements: "",
          },
        });
      }
    }

    await page.goto("/tests?page_size=10");
    await page.waitForSelector("table tbody tr");
    const rowCount = await page.locator("table tbody tr").count();
    expect(rowCount).toBeLessThanOrEqual(10);

    // Multi-page: a pagination region with "Next" should exist and be clickable.
    await expect(
      page.getByRole("button", { name: /next/i }).first(),
    ).toBeVisible();
  });
});

test.describe("Sessions on a test", () => {
  test("session POSTed via API renders on the test detail page", async ({
    page,
    request,
  }) => {
    // Create a fresh test via the UI so this run is self-contained.
    await page.goto("/tests/add");
    await fillCreateTestForm(page, {
      experimentId: generateTestId("e2e-sess"),
    });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/);
    const testId = page.url().split("/").pop()!;

    // Initially: "No sessions yet".
    await expect(page.getByText("No sessions yet")).toBeVisible();

    // Simulate the AC bridge POSTing a session for this test.
    const sessionId = `2026-04-17T${Math.floor(Math.random() * 1000)}`;
    const resp = await request.post(
      `http://localhost:8080/api/v1/tests/${testId}/sessions`,
      {
        data: {
          session_id: sessionId,
          track: "monza",
          car_model: "ferrari_488",
        },
      },
    );
    expect(resp.status()).toBe(200);

    // Reload — the session should now be visible.
    await page.reload();
    await expect(page.getByText(sessionId)).toBeVisible();
    await expect(page.getByText("monza")).toBeVisible();
    await expect(page.getByText("ferrari_488")).toBeVisible();
  });
});

test.describe("Activate and dirty-check", () => {
  // These tests mutate shared local DB/DCM state and can't run in parallel.
  test.describe.configure({ mode: "serial" });

  test("activate button bumps config_version", async ({ page, request }) => {
    // Create an isolated test so the config_version delta is unambiguous.
    await page.goto("/tests/add");
    await fillCreateTestForm(page, { experimentId: generateTestId("e2e-act") });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/);
    const testId = page.url().split("/").pop()!;

    const apiUrl = `http://localhost:8080/api/v1/tests/${testId}`;
    const before = await request.get(apiUrl).then((r) => r.json());

    await page.getByTestId("activate-test").click();
    await waitForToast(page, "Test reactivated");

    const after = await request.get(apiUrl).then((r) => r.json());
    expect(after.config_version).toBe(before.config_version + 1);
    expect(after.config_id).toBe(before.config_id);
  });

  test("save button is disabled until the form is dirty", async ({ page }) => {
    // Create an isolated test (empty requirements baseline) so the revert-
    // to-empty assertion is unambiguous — otherwise prior runs may have
    // saved requirements on the first seeded test.
    await page.goto("/tests/add");
    await fillCreateTestForm(page, {
      experimentId: generateTestId("e2e-dirty"),
    });
    await page.getByRole("button", { name: "Create Test" }).click();
    await page.waitForURL(/\/tests\/TST-\d+$/);
    const testId = page.url().split("/").pop()!;

    await page.goto(`/tests/${testId}/edit`);

    const save = page.getByTestId("save-test");
    await expect(save).toBeDisabled();

    const requirements = page.locator("#requirements");
    await requirements.fill("dirty-change");
    await expect(save).toBeEnabled();

    await requirements.fill("");
    await expect(save).toBeDisabled();
  });

  test("delete cleans every DCM version of the deleted test", async ({
    page,
    request,
  }) => {
    const DCM = "http://localhost:8001";

    async function countVersionsForTest(testId: string): Promise<{
      cid: string;
      count: number;
    }> {
      const cfgs = await request
        .get(`${DCM}/api/v1/configurations?type=experiment`)
        .then((r) => r.json());
      const cid = cfgs.data[0].id;
      const versions = await request
        .get(`${DCM}/api/v1/configurations/${cid}/versions`)
        .then((r) => r.json());
      let count = 0;
      for (const v of versions.data) {
        const content = await request
          .get(
            `${DCM}/api/v1/configurations/${cid}/versions/${v.metadata.version}/content`,
          )
          .then((r) => r.json());
        if (content.test_id === testId) count++;
      }
      return { cid, count };
    }

    // Pick the last live test (least likely to have been touched by earlier tests)
    // so this test remains idempotent across runs.
    const list = await request
      .get("http://localhost:8080/api/v1/tests?page_size=20")
      .then((r) => r.json());
    const testId = list.items[list.items.length - 1].test_id;

    await page.goto(`/tests/${testId}`);
    await page.waitForSelector('[data-testid="activate-test"]');

    // Simulate a busy session: two activates to pile orphans.
    await page.getByTestId("activate-test").click();
    await waitForToast(page, "Test reactivated");
    await page.waitForTimeout(300);
    await page.getByTestId("activate-test").click();
    await waitForToast(page, "Test reactivated");

    // Edit → change requirements → save (creates another version).
    await page.getByRole("link", { name: /Edit Test/ }).click();
    await page.waitForURL(new RegExp(`/tests/${testId}/edit`));
    await expect(page.getByTestId("save-test")).toBeDisabled();
    await page.locator("#requirements").fill("churn-before-delete");
    await expect(page.getByTestId("save-test")).toBeEnabled();
    await page.getByTestId("save-test").click();
    await page.waitForURL(new RegExp(`/tests/${testId}$`));

    const before = await countVersionsForTest(testId);
    expect(before.count).toBeGreaterThanOrEqual(3);

    // Delete via the UI.
    await page.getByRole("button", { name: "Delete Test" }).click();
    await page.getByRole("button", { name: "Delete" }).click();
    await page.waitForURL("**/tests");

    const after = await countVersionsForTest(testId);
    expect(after.count).toBe(0);
  });
});
