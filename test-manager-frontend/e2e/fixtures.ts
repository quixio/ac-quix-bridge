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
 * Fill the create-test form with valid data. Selects the first available
 * option in each of the four dropdowns (PC, Test Rig, Environment, Driver),
 * then fills the experiment_id input and optional requirements textarea.
 *
 * Assumes the backend already has at least one device/env/driver of each
 * kind seeded (e.g. via `scripts/load_snapshot.py`).
 */
export async function fillCreateTestForm(
  page: any,
  data: {
    experimentId: string;
    requirements?: string;
  },
) {
  // Four Radix Selects in DOM order: PC, Test Rig, Environment, Driver.
  const selects = page.getByRole("combobox");
  for (let i = 0; i < 4; i++) {
    await selects.nth(i).click();
    await page.getByRole("option").first().click();
  }
  await page.locator("#experiment_id").fill(data.experimentId);
  if (data.requirements) {
    await page.locator("#requirements").fill(data.requirements);
  }
}
