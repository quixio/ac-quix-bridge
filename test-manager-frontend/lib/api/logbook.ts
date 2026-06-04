/**
 * API client for Logbook Management
 * Provides methods to interact with /tests/{test_id}/logbook endpoints
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
} from "@/types/test";

export const logbookApi = {
  /**
   * List all logbook entries for a test
   */
  list: (
    testId: string,
    options?: { sessionId?: string; includeTestWide?: boolean },
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: Record<string, string | boolean> = {};
    if (options?.sessionId !== undefined) {
      params.session_id = options.sessionId;
    }
    if (options?.includeTestWide !== undefined) {
      params.include_test_wide = options.includeTestWide;
    }
    return apiGet<LogbookEntry[]>(
      `/tests/${testId}/logbook`,
      Object.keys(params).length > 0 ? params : undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Get a single logbook entry
   */
  get: (
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
   * Create a new logbook entry
   */
  create: (
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
  update: (
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
  delete: (
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
};
