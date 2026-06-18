import { test as base, expect } from "@playwright/test";

/**
 * E2E Test Fixtures
 *
 * Provides shared setup and helper functions for tests
 */

type TestFixtures = {
  // Add custom fixtures here if needed
};

export const test = base.extend<TestFixtures>({
  // Extend with custom fixtures if needed
});

export { expect };

/**
 * Helper function to generate unique test ID
 */
export function generateTestId(prefix: string = "test"): string {
  const timestamp = Date.now();
  const random = Math.floor(Math.random() * 1000);
  return `${prefix}-${timestamp}-${random}`;
}

/**
 * Helper function to wait for toast notification
 */
export async function waitForToast(page: any, expectedText?: string) {
  const toast = page.locator('[role="status"]').first();
  await toast.waitFor({ state: "visible", timeout: 5000 });

  if (expectedText) {
    await expect(toast).toContainText(expectedText);
  }

  return toast;
}

/**
 * Fill the create-test form with valid data. Picks the first option for
 * PC / Test Rig / Environment / Driver, creates + selects a unique Experiment
 * via the inline +Add dialog (experiment is now a managed entity, so the given
 * `experimentId` must exist), sets Mode, and fills optional requirements.
 *
 * Assumes the backend has at least one device/env/driver seeded
 * (e.g. via `scripts/load_snapshot.py`).
 */
export async function fillCreateTestForm(
  page: any,
  data: {
    experimentId: string;
    requirements?: string;
    mode?: "Easy" | "Medium" | "Pro";
  },
) {
  // Six Radix Selects in DOM order: 0 PC, 1 Test Rig, 2 Environment,
  // 3 Experiment, 4 Driver, 5 Mode.
  const selects = page.getByRole("combobox");
  for (const i of [0, 1, 2, 4]) {
    await selects.nth(i).click();
    await page.getByRole("option").first().click();
  }
  // Experiment: create + auto-select a unique one via the +Add dialog so
  // experiment-id assertions hold (free text is no longer accepted).
  await page.getByRole("button", { name: "Add experiment" }).click();
  await page.locator("#experiment-name").fill(data.experimentId);
  await page.getByRole("button", { name: "Create Experiment" }).click();
  await expect(page.locator("#experiment-name")).toBeHidden();
  await expect(selects.nth(3)).toContainText(data.experimentId);
  // Mode has no auto-default — set it explicitly (first option = Easy).
  await selects.nth(5).click();
  await page
    .getByRole("option", { name: data.mode ?? "Easy", exact: true })
    .click();
  // Always set requirements (clearing the last-used prefill) so the created
  // test has a deterministic baseline — empty unless specified.
  await page.locator("#requirements").fill(data.requirements ?? "");
}
