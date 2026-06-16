/**
 * API client for Tests
 * Provides methods to interact with /tests endpoints
 *
 * @internal - Do not import directly. Use the `useTestsApi()` hook instead:
 * ```typescript
 * import { useTestsApi } from "@/lib/hooks/use-api"
 *
 * const testsApi = useTestsApi()
 * const tests = await testsApi.list(params) // Auth auto-injected
 * ```
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type {
  Test,
  TestCreate,
  TestUpdate,
  TestQuery,
  TestFullData,
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
} from "@/types/test";
import type { PaginatedResponse } from "@/types/pagination";

export const testsApi = {
  /**
   * List all tests with optional filtering and pagination
   */
  list: (
    params?: TestQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<PaginatedResponse<Test>>(
      "/tests",
      params,
      token,
      refreshToken,
    );
  },

  /**
   * Get a single test by ID
   */
  get: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<Test>(`/tests/${testId}`, undefined, token, refreshToken);
  },

  /**
   * Get a test with all related data (files, logbook, links) in one request
   * Optimizes performance by eliminating 4 sequential requests
   */
  getFull: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<TestFullData>(
      `/tests/${testId}/full`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Create a new test
   */
  create: (
    data: TestCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Test>("/tests", data, token, refreshToken);
  },

  /**
   * Update an existing test
   */
  update: (
    testId: string,
    data: TestUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPut<Test>(`/tests/${testId}`, data, token, refreshToken);
  },

  /**
   * Delete a test
   */
  delete: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiDelete(`/tests/${testId}`, token, refreshToken);
  },

  /**
   * Activate a test — push its current content as a new DCM version so it
   * becomes the latest for bridge enrichment. No content change required.
   */
  activate: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Test>(`/tests/${testId}/activate`, {}, token, refreshToken);
  },

  /**
   * Get Quix Lake partition parameters for a test (from Dynamic Config Manager)
   */
  getTelemetryParams: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<{
      environment: string;
      test_rig: string;
      experiment: string;
      driver: string;
      // null when the test has no sessions yet (no track/car to pin).
      track: string | null;
      carModel: string | null;
    }>(`/tests/${testId}/telemetry-params`, undefined, token, refreshToken);
  },

  // ========================================================================
  // Logbook Entries
  // ========================================================================

  /**
   * List logbook entries for a test
   */
  listLogbook: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<LogbookEntry[]>(
      `/tests/${testId}/logbook`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Get a single logbook entry
   */
  getLogbookEntry: (
    testId: string,
    entryId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<LogbookEntry>(
      `/tests/${testId}/logbook/${entryId}`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Create a logbook entry
   */
  createLogbookEntry: (
    testId: string,
    data: LogbookEntryCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<LogbookEntry>(
      `/tests/${testId}/logbook`,
      data,
      token,
      refreshToken,
    );
  },

  /**
   * Update a logbook entry
   */
  updateLogbookEntry: (
    testId: string,
    entryId: string,
    data: LogbookEntryUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPut<LogbookEntry>(
      `/tests/${testId}/logbook/${entryId}`,
      data,
      token,
      refreshToken,
    );
  },

  /**
   * Delete a logbook entry
   */
  deleteLogbookEntry: (
    testId: string,
    entryId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiDelete(
      `/tests/${testId}/logbook/${entryId}`,
      token,
      refreshToken,
    );
  },

  // ========================================================================
  // Filters (Distinct Values for Autocomplete)
  // ========================================================================

  getExperimentIds: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<string[]>(
      "/tests/filters/experiment-ids",
      undefined,
      token,
      refreshToken,
    );
  },

  getEnvironmentIds: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<string[]>(
      "/tests/filters/environment-ids",
      undefined,
      token,
      refreshToken,
    );
  },

  getDrivers: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<string[]>(
      "/tests/filters/drivers",
      undefined,
      token,
      refreshToken,
    );
  },
};
