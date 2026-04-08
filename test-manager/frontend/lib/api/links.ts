/**
 * API client for External Links Management
 * Provides methods to interact with /tests/{test_id}/links endpoints
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client"
import type { Link, LinkCreate } from "@/types/test"

export const linksApi = {
  /**
   * List all links for a test
   */
  list: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Link[]>(`/tests/${testId}/links`, undefined, token, refreshToken)
  },

  /**
   * Create a new link
   */
  create: (
    testId: string,
    data: LinkCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<Link>(`/tests/${testId}/links`, data, token, refreshToken)
  },

  /**
   * Update an existing link
   */
  update: (
    testId: string,
    linkId: string,
    data: LinkCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPut<Link>(`/tests/${testId}/links/${linkId}`, data, token, refreshToken)
  },

  /**
   * Delete a link
   */
  delete: (
    testId: string,
    linkId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiDelete(`/tests/${testId}/links/${linkId}`, token, refreshToken)
  },
}
