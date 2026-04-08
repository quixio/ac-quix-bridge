/**
 * API client for Admin operations
 * Provides methods to interact with /admin endpoints
 */

import { apiPost } from "./client"

export interface SeedDataRequest {
  num_dacs: number
  num_tests: number
  include_journals: boolean
  include_logbook: boolean
}

export interface SeedDataResponse {
  dacs_created: number
  tests_created: number
  journal_entries_created: number
  logbook_entries_created: number
  message: string
}

export const adminApi = {
  /**
   * Seed demo data with parameterized quantities
   */
  seedTestData: (
    request: SeedDataRequest,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    const params = new URLSearchParams({
      num_dacs: request.num_dacs.toString(),
      num_tests: request.num_tests.toString(),
      include_journals: request.include_journals.toString(),
      include_logbook: request.include_logbook.toString(),
    })

    return apiPost<SeedDataResponse>(`/admin/seed-demo-data?${params.toString()}`, {}, token, refreshToken)
  },
}
