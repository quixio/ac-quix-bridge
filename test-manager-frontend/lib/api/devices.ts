/**
 * API client for Devices
 *
 * @internal - Do not import directly. Use the `useDevicesApi()` hook instead.
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type {
  Device,
  DeviceCreate,
  DeviceUpdate,
  DeviceQuery,
} from "@/types/device";
import type { PaginatedResponse } from "@/types/pagination";

export const devicesApi = {
  list: (
    params?: DeviceQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<PaginatedResponse<Device>>(
      "/devices",
      params,
      token,
      refreshToken,
    );
  },

  get: (
    deviceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<Device>(
      `/devices/${deviceId}`,
      undefined,
      token,
      refreshToken,
    );
  },

  getBatch: (
    deviceIds: string[],
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Device[]>("/devices/batch", deviceIds, token, refreshToken);
  },

  create: (
    data: DeviceCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Device>("/devices", data, token, refreshToken);
  },

  update: (
    deviceId: string,
    data: DeviceUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPut<Device>(`/devices/${deviceId}`, data, token, refreshToken);
  },

  delete: (
    deviceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiDelete(`/devices/${deviceId}`, token, refreshToken);
  },
};
