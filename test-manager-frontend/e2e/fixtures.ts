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
 * Helper function to fill test form with valid data
 */
export async function fillTestForm(
  page: any,
  data: {
    test_id: string;
    campaign_id: string;
    environment_id: string;
    operator: string;
  },
) {
  await page.fill("#test_id", data.test_id);
  await page.fill("#campaign_id", data.campaign_id);
  await page.fill("#environment_id", data.environment_id);
  await page.fill("#operator", data.operator);
}
