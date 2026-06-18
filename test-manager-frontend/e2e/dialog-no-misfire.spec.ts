import { test, expect, generateTestId } from "./fixtures";

/**
 * Regression: the inline "+Add" dialogs on the test forms (experiment / driver)
 * are portaled, but React replays their <form> submit through the portal up the
 * component tree into the surrounding test <form>. Without stopPropagation, that
 * fires the outer form's onSubmit — creating a stray test (create page) or
 * silently re-saving / re-versioning the test (edit page) and navigating away.
 *
 * Contract under test: creating an entity from a +Add dialog must NOT navigate
 * away from the form. Each test waits for the dialog to close (the create
 * handler finished) then a short settle for any errant redirect, then asserts
 * the URL is unchanged.
 */

const SETTLE_MS = 800;

async function addExperiment(page: any) {
  await page.getByRole("button", { name: "Add experiment" }).click();
  await page.locator("#experiment-name").fill(generateTestId("e2e-bubble"));
  await page.getByRole("button", { name: "Create Experiment" }).click();
  await expect(page.locator("#experiment-name")).toBeHidden();
}

async function addDriver(page: any) {
  await page.getByRole("button", { name: "Add driver" }).click();
  await page.locator("#name").fill(generateTestId("E2E Bubble Driver"));
  await page.locator("#email").fill(`e2e-bubble-${Date.now()}@example.com`);
  await page.locator("#company").fill("E2E Co");
  await page.getByRole("button", { name: "Create Driver" }).click();
  await expect(page.locator("#name")).toBeHidden();
}

test.describe("+Add dialogs must not submit the surrounding test form", () => {
  test("adding an experiment on the create form does not create a test", async ({
    page,
  }) => {
    await page.goto("/tests/add");
    await expect(
      page.getByRole("button", { name: "Create Test" }),
    ).toBeVisible();

    await addExperiment(page);
    await page.waitForTimeout(SETTLE_MS);
    await expect(page).toHaveURL(/\/tests\/add$/);
  });

  test("adding a driver on the create form does not create a test", async ({
    page,
  }) => {
    await page.goto("/tests/add");
    await expect(
      page.getByRole("button", { name: "Create Test" }),
    ).toBeVisible();

    await addDriver(page);
    await page.waitForTimeout(SETTLE_MS);
    await expect(page).toHaveURL(/\/tests\/add$/);
  });

  test.describe("on the edit form", () => {
    test.beforeEach(async ({ page }) => {
      // Open the first existing test's edit page.
      await page.goto("/tests");
      await page.waitForSelector("table tbody tr", { timeout: 10000 });
      await page.locator("table tbody tr").first().click();
      await page.waitForURL(/\/tests\/TST-\d+$/, { timeout: 10000 });
      await page.locator('a:has-text("Edit Test")').first().click();
      await page.waitForURL(/\/tests\/TST-\d+\/edit$/, { timeout: 10000 });
    });

    test("adding an experiment does not re-save the test", async ({ page }) => {
      await addExperiment(page);
      await page.waitForTimeout(SETTLE_MS);
      await expect(page).toHaveURL(/\/tests\/TST-\d+\/edit$/);
    });

    test("adding a driver does not re-save the test", async ({ page }) => {
      await addDriver(page);
      await page.waitForTimeout(SETTLE_MS);
      await expect(page).toHaveURL(/\/tests\/TST-\d+\/edit$/);
    });
  });
});
