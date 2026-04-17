import { test, expect } from './fixtures';

/**
 * E2E interaction tests for the Leaderboard tab (spec §6.8).
 *
 * Runner note: the repo only has Playwright configured. No jest / vitest.
 * Per the task, the frontend interaction-test scope falls to Playwright
 * with full route interception (no real backend required).
 *
 * These tests mock the `/api/v1/leaderboard/best-laps` endpoint at the
 * Playwright layer so the dev server (`npm run dev`) can be up with or
 * without a real backend. A counter on the mock captures the number of
 * fetches — this is how we prove the "one fetch on mount, no re-fetch on
 * filter change" contract from spec §S2–S4.
 *
 * Requires a running dev server at http://localhost:3000 (the default
 * `playwright.config.ts` baseURL). Playwright's `webServer` is commented
 * out in the config, so start `npm run dev` manually before running these.
 */

const LEADERBOARD_ROUTE = '**/api/v1/leaderboard/best-laps**';

/**
 * Mock data designed so the first-alphabetical triple
 *   (Track=ks_nurburgring, Car=bmw_1m, Experiment=exp_42)
 * always has matching rows. All rows share the SAME (track, car), so the
 * Track/Car defaults are stable and every Experiment option has data in
 * that scope — this avoids the S5 "empty combination" render which would
 * otherwise kick in when the first-alphabetical Track / Car / Experiment
 * triple happens to lack matching rows (an implementation detail of spec
 * §S1 "pre-select first alphabetical in each").
 */
const MOCK_PAYLOAD = [
  {
    track: 'ks_nurburgring',
    car: 'bmw_1m',
    experiment: 'exp_42',
    driver: 'Ludvík',
    best_lap_ms: 98342,
    session_id: null,
    lap_number: null,
    achieved_at: null,
  },
  {
    track: 'ks_nurburgring',
    car: 'bmw_1m',
    experiment: 'exp_42',
    driver: 'Alice',
    best_lap_ms: 99108,
    session_id: null,
    lap_number: null,
    achieved_at: null,
  },
  // A second experiment on the same (track, car) so we can toggle the
  // Experiment dropdown and observe a re-rank without a new network call.
  {
    track: 'ks_nurburgring',
    car: 'bmw_1m',
    experiment: 'exp_43',
    driver: 'Alice',
    best_lap_ms: 95500,
    session_id: null,
    lap_number: null,
    achieved_at: null,
  },
  {
    track: 'ks_nurburgring',
    car: 'bmw_1m',
    experiment: 'exp_43',
    driver: 'Ludvík',
    best_lap_ms: 97000,
    session_id: null,
    lap_number: null,
    achieved_at: null,
  },
];

/**
 * Seed a fake auth token into localStorage so the MainLayout auth gate
 * doesn't block the tab from mounting. Same trick the app uses in
 * standalone mode (see lib/api/client.ts::getAuthToken).
 */
async function seedStandaloneAuthToken(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    // Key comes from quix-auth-context::STANDALONE_TOKEN_KEY.
    // (See lib/contexts/quix-auth-context.tsx — exports
    // `STANDALONE_TOKEN_KEY = "quix_standalone_auth_token"`.)
    localStorage.setItem('quix_standalone_auth_token', 'mock-token-for-e2e');
  });
}

test.describe('Leaderboard tab', () => {
  test('fetches once on tab mount', async ({ page }) => {
    /** Validates spec §5.6 + §S1: a bounded number of GETs fire when the
     * tab mounts — NOT one per row, one per filter option, or N-on-loop.
     *
     * Note: `reactStrictMode: true` in `next.config.js` causes effects to
     * double-invoke in dev mode (that is the well-known React 18 Strict
     * Mode contract). A production build would fire exactly once. We
     * therefore accept `<= 2` here and rely on the follow-up tests to
     * prove the effect doesn't re-fire on filter change. */
    await seedStandaloneAuthToken(page);

    let fetchCount = 0;
    await page.route(LEADERBOARD_ROUTE, async (route) => {
      if (route.request().method() === 'GET') {
        fetchCount++;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PAYLOAD),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    // Wait for the table to render so we know the fetch + first render have
    // finished before we assert the fetch count.
    await expect(page.getByRole('cell', { name: /Ludvík/ })).toBeVisible({ timeout: 15000 });

    expect(
      fetchCount,
      'mount-phase fetch count (1 in prod, up to 2 with React Strict Mode)'
    ).toBeLessThanOrEqual(2);
    expect(fetchCount, 'mount fired at least once').toBeGreaterThanOrEqual(1);
  });

  test('dropdown options are derived from the payload (not a second fetch)', async ({ page }) => {
    /** Validates spec §5.6 + §7.2: no separate filter-options endpoints;
     * Track / Car / Experiment dropdowns are populated from the payload's
     * distinct partition values. */
    await seedStandaloneAuthToken(page);

    let fetchCount = 0;
    await page.route(LEADERBOARD_ROUTE, async (route) => {
      if (route.request().method() === 'GET') fetchCount++;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PAYLOAD),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    await expect(page.getByRole('cell', { name: /Ludvík/ })).toBeVisible({ timeout: 15000 });

    // Snapshot the mount-phase fetch count (1 in prod, up to 2 under Strict Mode).
    const mountFetchCount = fetchCount;

    // Open Track dropdown and check its only derived option.
    await page.getByRole('combobox').nth(0).click();
    await expect(page.getByRole('option', { name: 'ks_nurburgring' })).toBeVisible();
    // Close it.
    await page.keyboard.press('Escape');

    // Open Experiment dropdown — distinct values from the payload.
    await page.getByRole('combobox').nth(2).click();
    await expect(page.getByRole('option', { name: 'exp_42' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'exp_43' })).toBeVisible();
    await page.keyboard.press('Escape');

    expect(
      fetchCount,
      'no new fetch should fire from opening dropdowns'
    ).toBe(mountFetchCount);
  });

  test('changing a dropdown re-filters and re-ranks without a network call', async ({ page }) => {
    /** Validates spec §S2/S3/S4 + §5.6: dropdown changes do NOT trigger a
     * new fetch; the table re-ranks in-memory.
     *
     * Default selection is alphabetical first, so:
     *   Track=ks_nurburgring, Car=bmw_1m, Experiment=exp_42
     * → rows: Ludvík 98342 (rank 1), Alice 99108 (rank 2)
     * Then we change Experiment to exp_43:
     * → rows: Alice 95500 (rank 1), Ludvík 97000 (rank 2)
     * Same data, no fetch. */
    await seedStandaloneAuthToken(page);

    let fetchCount = 0;
    await page.route(LEADERBOARD_ROUTE, async (route) => {
      if (route.request().method() === 'GET') fetchCount++;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PAYLOAD),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    await expect(page.getByRole('cell', { name: /Ludvík/ })).toBeVisible({ timeout: 15000 });

    // Initial state (exp_42): Ludvík first, Alice second.
    const rowsInitial = page.getByRole('row');
    // Header + 2 data rows
    await expect(rowsInitial).toHaveCount(3);
    const initialDrivers = await page
      .locator('table tbody tr td:nth-child(2)')
      .allInnerTexts();
    expect(initialDrivers).toEqual(['Ludvík', 'Alice']);

    // Snapshot fetch count BEFORE changing any filter.
    const fetchesAfterMount = fetchCount;

    // Change Experiment to exp_43.
    await page.getByRole('combobox').nth(2).click();
    await page.getByRole('option', { name: 'exp_43' }).click();

    // After the re-rank, Alice is first (95500), Ludvík second (97000).
    await expect(page.locator('table tbody tr')).toHaveCount(2);
    const laterDrivers = await page
      .locator('table tbody tr td:nth-child(2)')
      .allInnerTexts();
    expect(laterDrivers).toEqual(['Alice', 'Ludvík']);

    expect(
      fetchCount,
      'filter change must not trigger a new network request'
    ).toBe(fetchesAfterMount);
  });

  test('empty lake shows the empty-state CTA (S6)', async ({ page }) => {
    /** Validates spec §S6 + arch doc §3 "Empty states": backend returns
     * []; frontend renders EmptyState with an `Add Test` action. */
    await seedStandaloneAuthToken(page);

    await page.route(LEADERBOARD_ROUTE, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    await expect(page.getByText(/No laps recorded yet/)).toBeVisible({ timeout: 15000 });
    // The spec S6 mandates a link to /tests/add.
    await expect(page.getByRole('button', { name: /Add Test/ })).toBeVisible();
    // There should be NO table headers when the empty-lake CTA is up.
    await expect(page.getByRole('cell', { name: /Rank/ })).toHaveCount(0);
  });

  test('501 response shows the "configure measurements" empty-state (S7)', async ({ page }) => {
    /** Validates spec §S7 + §7.4: backend returns 501 → EmptyState points
     * at `/settings`. */
    await seedStandaloneAuthToken(page);

    await page.route(LEADERBOARD_ROUTE, async (route) => {
      await route.fulfill({
        status: 501,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'Measurements service not configured. Configure it in Settings.',
        }),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    await expect(page.getByText(/Measurements service not configured/i)).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByRole('button', { name: /Open Settings/ })).toBeVisible();
  });

  test('an experiment with zero valid laps does NOT appear in the dropdown', async ({ page }) => {
    /** Validates spec Q1 / §5.6 / §R6: filter options come from the
     * aggregated payload. An experiment that produced zero valid laps
     * (i.e. has no rows in the response) must not appear in the dropdown.
     * Here `exp_silent` is NOT in the payload, so it must not show up. */
    await seedStandaloneAuthToken(page);

    await page.route(LEADERBOARD_ROUTE, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_PAYLOAD),
      });
    });

    await page.goto('/analysis?tab=leaderboard');
    await expect(page.getByRole('cell', { name: /Ludvík/ })).toBeVisible({ timeout: 15000 });

    // Open Experiment dropdown.
    await page.getByRole('combobox').nth(2).click();
    await expect(page.getByRole('option', { name: 'exp_42' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'exp_43' })).toBeVisible();
    // Must NOT contain exp_silent or any other value that isn't in the payload.
    await expect(page.getByRole('option', { name: 'exp_silent' })).toHaveCount(0);
  });
});
