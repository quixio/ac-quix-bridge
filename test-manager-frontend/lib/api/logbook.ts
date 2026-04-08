/**
 * API client for Logbook Management
 * Provides methods to interact with /tests/{test_id}/logbook endpoints
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client"
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
} from "@/types/test"

export const logbookApi = {
  /**
   * List all logbook entries for a test
   */
  list: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<LogbookEntry[]>(`/tests/${testId}/logbook`, undefined, token, refreshToken)
  },

  /**
   * Get a single logbook entry
   */
  get: (
    testId: string,
    entryId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<LogbookEntry>(`/tests/${testId}/logbook/${entryId}`, undefined, token, refreshToken)
  },

  /**
   * Create a new logbook entry
   */
  create: (
    testId: string,
    data: LogbookEntryCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<LogbookEntry>(`/tests/${testId}/logbook`, data, token, refreshToken)
  },

  /**
   * Update a logbook entry
   */
  update: (
    testId: string,
    entryId: string,
    data: LogbookEntryUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPut<LogbookEntry>(`/tests/${testId}/logbook/${entryId}`, data, token, refreshToken)
  },

  /**
   * Delete a logbook entry
   */
  delete: (
    testId: string,
    entryId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiDelete(`/tests/${testId}/logbook/${entryId}`, token, refreshToken)
  },
}
