/**
 * API client for Environments
 *
 * @internal - Do not import directly. Use the `useEnvironmentsApi()` hook instead.
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client"
import type { Environment, EnvironmentCreate, EnvironmentUpdate, EnvironmentQuery } from "@/types/environment"
import type { PaginatedResponse } from "@/types/pagination"

export const environmentsApi = {
  list: (
    params?: EnvironmentQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<PaginatedResponse<Environment>>("/environments", params, token, refreshToken)
  },

  get: (
    environmentId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Environment>(`/environments/${environmentId}`, undefined, token, refreshToken)
  },

  create: (
    data: EnvironmentCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<Environment>("/environments", data, token, refreshToken)
  },

  update: (
    environmentId: string,
    data: EnvironmentUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPut<Environment>(`/environments/${environmentId}`, data, token, refreshToken)
  },

  delete: (
    environmentId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiDelete(`/environments/${environmentId}`, token, refreshToken)
  },
}
