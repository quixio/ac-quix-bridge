import {
  test,
  expect,
  generateTestId,
  waitForToast,
  fillTestForm,
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
    // Wait for Add Test link to be ready and click it
    const addTestLink = page.locator('a:has-text("Add Test")');
    await addTestLink.waitFor({ state: "visible", timeout: 5000 });
    await addTestLink.click();

    // Wait for navigation to complete
    await page.waitForURL("/tests/add", { timeout: 10000 });

    // Verify we're on create page
    await expect(page).toHaveURL("/tests/add");
    await expect(
      page.getByRole("heading", { name: "Create New Test" }),
    ).toBeVisible();
  });

  test("should show validation errors on empty form submission", async ({
    page,
  }) => {
    // Navigate to create page
    await page.goto("/tests/add");

    // Try to submit without filling any fields
    await page.getByRole("button", { name: "Create Test" }).click();

    // Verify validation errors appear
    await expect(page.getByText("Test ID is required")).toBeVisible();
    await expect(page.getByText("Campaign ID is required")).toBeVisible();
    await expect(page.getByText("Environment ID is required")).toBeVisible();
    await expect(page.getByText("Operator is required")).toBeVisible();
    await expect(
      page.getByText("At least one Device is required"),
    ).toBeVisible();
  });

  test("should create a new test successfully", async ({ page }) => {
    // Navigate to create page
    await page.goto("/tests/add");

    // Generate unique test ID
    const testId = generateTestId("e2e-test");

    // Fill in required fields
    await fillTestForm(page, {
      test_id: testId,
      campaign_id: "e2e-campaign",
      environment_id: "e2e-environment",
      operator: "E2E Test Operator",
    });

    // Search for and select a Device (search for common patterns)
    const deviceSearch = page.getByPlaceholder(
      "Quick search by Device ID or Sample ID...",
    );

    // Try multiple search patterns to find a Device
    const searchPatterns = ["TEST", "DEVICE", "SAMPLE", "2025", "001"];
    let deviceSelected = false;

    for (const pattern of searchPatterns) {
      if (deviceSelected) break;

      await deviceSearch.fill(pattern);

      try {
        const firstDeviceResult = page.locator("[data-device-result]").first();
        await firstDeviceResult.waitFor({ state: "visible", timeout: 3000 });
        await firstDeviceResult.click();
        deviceSelected = true;
        console.log(
          `Successfully selected Device with search pattern: ${pattern}`,
        );
      } catch (error) {
        // Try next pattern
        continue;
      }
    }

    if (!deviceSelected) {
      // Skip this test if no Devices are available in the system
      console.warn("No Devices found - skipping test");
      test.skip();
      return;
    }

    // Click away from any datetime picker before submitting
    await page.locator("h1").click();

    // Submit form
    await page.getByRole("button", { name: "Create Test" }).click();

    // Wait for redirect to detail page
    await page.waitForURL(`/tests/${testId}`, { timeout: 10000 });

    // Verify we're on the detail page with correct test ID
    await expect(page.getByRole("heading", { name: testId })).toBeVisible();

    // Verify test details are displayed
    await expect(page.getByText("Test execution details")).toBeVisible();
  });

  test("should edit an existing test", async ({ page }) => {
    // First, create a test to edit
    await page.goto("/tests/add");

    const testId = generateTestId("e2e-edit-test");

    await fillTestForm(page, {
      test_id: testId,
      campaign_id: "e2e-edit-campaign",
      environment_id: "e2e-environment",
      operator: "E2E Edit Test",
    });

    // Try to add Device
    const deviceSearch = page.getByPlaceholder(
      "Quick search by Device ID or Sample ID...",
    );
    await deviceSearch.fill("DEVICE");

    try {
      const firstDeviceResult = page.locator("[data-device-result]").first();
      await firstDeviceResult.waitFor({ state: "visible", timeout: 5000 });
      await firstDeviceResult.click();
    } catch (error) {
      console.warn("No Devices found - skipping edit test");
      test.skip();
      return;
    }

    // Submit to create test
    await page.getByRole("button", { name: "Create Test" }).click();

    // Wait for creation
    await page.waitForURL(`/tests/${testId}`, { timeout: 10000 });

    // Now test editing
    await page.getByRole("link", { name: "Edit Test" }).click();

    // Verify we're on edit page
    await expect(
      page.getByRole("heading", { name: /Edit Test:/ }),
    ).toBeVisible();

    // Modify operator field
    const operatorField = page.locator("#operator");
    await operatorField.clear();
    await operatorField.fill("Updated Operator - E2E Test");

    // Click away from any input before submitting
    await page.locator("h1").click();

    // Submit form
    await page.getByRole("button", { name: "Update Test" }).click();

    // Wait a moment for the update to process
    await page.waitForTimeout(1000);

    // Verify we're back on detail page by checking for Edit Test link
    await expect(page.getByRole("link", { name: "Edit Test" })).toBeVisible();

    // Verify the updated operator value is displayed
    await expect(page.getByText("Updated Operator - E2E Test")).toBeVisible();
  });

  test("should delete a test with confirmation", async ({ page }) => {
    // Create a test first for deletion
    await page.goto("/tests/add");

    const testId = generateTestId("e2e-delete-test");

    await fillTestForm(page, {
      test_id: testId,
      campaign_id: "e2e-delete-campaign",
      environment_id: "e2e-environment",
      operator: "E2E Delete Test",
    });

    // Try to add Device (may fail if no Devices available)
    const deviceSearch = page.getByPlaceholder(
      "Quick search by Device ID or Sample ID...",
    );
    await deviceSearch.fill("DEVICE");

    try {
      const firstDeviceResult = page.locator("[data-device-result]").first();
      await firstDeviceResult.waitFor({ state: "visible", timeout: 5000 });
      await firstDeviceResult.click();
    } catch (error) {
      console.warn("No Devices found in search - test may fail");
    }

    // Submit to create test
    await page.getByRole("button", { name: "Create Test" }).click();

    // Wait for creation
    await page.waitForURL(`/tests/${testId}`, { timeout: 10000 }).catch(() => {
      // If creation failed, skip delete test
      test.skip();
    });

    // Now test deletion
    await page.getByRole("button", { name: "Delete Test" }).click();

    // Verify confirmation dialog appears
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await expect(page.getByText(/permanently delete test/i)).toBeVisible();

    // Confirm deletion
    await page
      .getByRole("button", { name: "Delete", exact: true })
      .last()
      .click();

    // Wait for redirect to tests list
    await page.waitForURL("/tests", { timeout: 10000 });

    // Verify we're back on tests list page
    await expect(page.getByRole("heading", { name: "Tests" })).toBeVisible();
  });

  test("should cancel test creation", async ({ page }) => {
    await page.goto("/tests/add");

    // Fill some data
    await page.fill("#test_id", "cancel-test");

    // Click Cancel button
    await page.getByRole("button", { name: "Cancel" }).click();

    // Verify we're back on tests list
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

test.describe("Activate and dirty-check", () => {
  // These tests mutate shared local DB/DCM state and can't run in parallel.
  test.describe.configure({ mode: "serial" });

  // Picks any pre-seeded test via the tests list rather than a hardcoded id.
  async function openFirstTestDetail(page: any): Promise<void> {
    await page.goto("/tests");
    await page.waitForSelector("table tbody tr", { timeout: 10000 });
    await page.locator("table tbody tr").first().click();
    await page.waitForURL(/\/tests\/TST-/);
  }

  test("activate button bumps config_version", async ({ page }) => {
    await openFirstTestDetail(page);

    // Read config_version from detail card (rendered as "Version N" or similar).
    const url = page.url();
    const testId = url.split("/").pop()!;
    const apiUrl = `${url.replace(`/tests/${testId}`, "")}/api/v1/tests/${testId}`;
    const before = await page.evaluate(
      (u) => fetch(u).then((r) => r.json()),
      apiUrl,
    );

    await page.getByTestId("activate-test").click();
    await waitForToast(page, "Test activated");

    const after = await page.evaluate(
      (u) => fetch(u).then((r) => r.json()),
      apiUrl,
    );
    expect(after.config_version).toBe(before.config_version + 1);
    expect(after.config_id).toBe(before.config_id);
  });

  test("save button is disabled until the form is dirty", async ({ page }) => {
    await openFirstTestDetail(page);

    const url = page.url();
    const testId = url.split("/").pop()!;
    await page.goto(`/tests/${testId}/edit`);

    const save = page.getByTestId("save-test");
    await expect(save).toBeDisabled();

    // Tweak the requirements textarea to dirty the form.
    const requirements = page.locator("#requirements");
    await requirements.fill("dirty-change");
    await expect(save).toBeEnabled();

    // Revert and it should be disabled again.
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
    await waitForToast(page, "Test activated");
    await page.waitForTimeout(300);
    await page.getByTestId("activate-test").click();
    await waitForToast(page, "Test activated");

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
