/**
 * API client for Drivers
 *
 * @internal - Do not import directly. Use the `useDriversApi()` hook instead.
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type {
  Driver,
  DriverCreate,
  DriverUpdate,
  DriverQuery,
} from "@/types/driver";
import type { PaginatedResponse } from "@/types/pagination";

export const driversApi = {
  list: (
    params?: DriverQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<PaginatedResponse<Driver>>(
      "/drivers",
      params,
      token,
      refreshToken,
    );
  },

  get: (
    driverId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<Driver>(
      `/drivers/${driverId}`,
      undefined,
      token,
      refreshToken,
    );
  },

  create: (
    data: DriverCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Driver>("/drivers", data, token, refreshToken);
  },

  update: (
    driverId: string,
    data: DriverUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPut<Driver>(`/drivers/${driverId}`, data, token, refreshToken);
  },

  delete: (
    driverId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiDelete(`/drivers/${driverId}`, token, refreshToken);
  },
};
